#include "ufbx.h"
#include "ufbx_write.h"

#include <algorithm>
#include <charconv>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <unordered_map>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

namespace fs = std::filesystem;

namespace {

constexpr uint64_t kDefaultMaxFaces = 50'000'000;

struct Arguments {
    fs::path input;
    fs::path output;
    fs::path levels_file;
    uint32_t level = 0;
    uint64_t max_faces = kDefaultMaxFaces;
    bool binary_input = false;
    bool output_fbx = false;
    bool show_help = false;
    bool show_version = false;
};

struct MaterialInfo {
    std::string name;
    ufbx_vec3 color = {0.8, 0.8, 0.8};
};

struct SimpleFace {
    std::vector<uint32_t> vertices;
    uint32_t material = UFBX_NO_INDEX;
};

struct SimpleMesh {
    std::vector<ufbx_vec3> vertices;
    std::vector<SimpleFace> faces;
};

// Keep formatted text out of iostream's per-value formatting path. OBJ output
// is large enough that converting every coordinate and face index separately
// through operator<< dominated the subdivision work in profiling.
class FastObjWriter {
public:
    explicit FastObjWriter(std::ostream &stream)
        : stream_(stream), buffer_(kBufferSize) {}

    bool append_text(std::string_view text)
    {
        while (!text.empty() && good_) {
            if (used_ == buffer_.size() && !flush()) return false;
            const size_t count = std::min(text.size(), buffer_.size() - used_);
            std::memcpy(buffer_.data() + used_, text.data(), count);
            used_ += count;
            text.remove_prefix(count);
        }
        return good_;
    }

    bool append_char(char value)
    {
        if (!good_) return false;
        if (used_ == buffer_.size() && !flush()) return false;
        buffer_[used_++] = value;
        return true;
    }

    bool append_double(double value)
    {
        char text[64];
        const auto result = std::to_chars(
            text, text + sizeof(text), value, std::chars_format::general, 17);
        if (result.ec != std::errc()) {
            good_ = false;
            return false;
        }
        return append_text(std::string_view(text, static_cast<size_t>(result.ptr - text)));
    }

    bool append_index(uint64_t value)
    {
        char text[32];
        const auto result = std::to_chars(text, text + sizeof(text), value);
        if (result.ec != std::errc()) {
            good_ = false;
            return false;
        }
        return append_text(std::string_view(text, static_cast<size_t>(result.ptr - text)));
    }

    bool flush()
    {
        if (!good_) return false;
        if (used_ != 0) {
            stream_.write(buffer_.data(), static_cast<std::streamsize>(used_));
            used_ = 0;
            good_ = stream_.good();
        }
        return good_;
    }

    bool good() const { return good_ && stream_.good(); }

private:
    static constexpr size_t kBufferSize = 4u * 1024u * 1024u;
    std::ostream &stream_;
    std::vector<char> buffer_;
    size_t used_ = 0;
    bool good_ = true;
};

struct EdgeData {
    uint32_t a = 0;
    uint32_t b = 0;
    std::vector<uint32_t> faces;
    uint32_t point_index = 0;
};

ufbx_vec3 add(ufbx_vec3 a, ufbx_vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
ufbx_vec3 mul(ufbx_vec3 a, double s) { return {a.x * s, a.y * s, a.z * s}; }
ufbx_vec3 divv(ufbx_vec3 a, double s) { return s != 0.0 ? mul(a, 1.0 / s) : ufbx_vec3{0.0, 0.0, 0.0}; }

uint64_t edge_key(uint32_t a, uint32_t b)
{
    if (a > b) std::swap(a, b);
    return (uint64_t(a) << 32u) | uint64_t(b);
}

bool make_simple_mesh(const ufbx_mesh *source, SimpleMesh &result, size_t &skipped_faces, std::string &error)
{
    result.vertices.assign(source->vertices.data, source->vertices.data + source->vertices.count);
    result.faces.clear();
    result.faces.reserve(source->faces.count);
    skipped_faces = 0;

    for (size_t face_index = 0; face_index < source->faces.count; ++face_index) {
        const ufbx_face source_face = source->faces.data[face_index];
        if (source_face.index_begin > source->vertex_indices.count ||
            source_face.num_indices > source->vertex_indices.count - source_face.index_begin) {
            error = "Face index range is outside the FBX index buffer";
            return false;
        }

        SimpleFace face;
        face.vertices.reserve(source_face.num_indices);
        for (uint32_t i = 0; i < source_face.num_indices; ++i) {
            uint32_t vertex = source->vertex_indices.data[source_face.index_begin + i];
            if (vertex >= result.vertices.size()) {
                error = "Polygon refers to a vertex outside the FBX vertex buffer";
                return false;
            }
            if (face.vertices.empty() || face.vertices.back() != vertex) {
                face.vertices.push_back(vertex);
            }
        }
        if (face.vertices.size() > 1 && face.vertices.front() == face.vertices.back()) {
            face.vertices.pop_back();
        }

        uint32_t first = UFBX_NO_INDEX, second = UFBX_NO_INDEX;
        bool has_three_distinct = false;
        for (uint32_t vertex : face.vertices) {
            if (first == UFBX_NO_INDEX) first = vertex;
            else if (vertex != first && second == UFBX_NO_INDEX) second = vertex;
            else if (vertex != first && vertex != second) {
                has_three_distinct = true;
                break;
            }
        }
        if (!has_three_distinct) {
            ++skipped_faces;
            continue;
        }
        if (source->face_material.count > face_index) {
            face.material = source->face_material.data[face_index];
        }
        result.faces.push_back(std::move(face));
    }
    return true;
}

SimpleMesh subdivide_once(const SimpleMesh &source)
{
    const size_t num_vertices = source.vertices.size();
    const size_t num_faces = source.faces.size();
    size_t corner_count = 0;
    for (const SimpleFace &face : source.faces) corner_count += face.vertices.size();

    std::vector<ufbx_vec3> face_points(num_faces, {0.0, 0.0, 0.0});
    std::vector<uint32_t> face_edge_offsets(num_faces);
    std::vector<uint32_t> corner_edges;
    corner_edges.reserve(corner_count);
    std::vector<std::vector<uint32_t>> vertex_faces(num_vertices);
    std::vector<std::vector<uint32_t>> vertex_edges(num_vertices);
    std::unordered_map<uint64_t, uint32_t> edge_lookup;
    std::vector<EdgeData> edges;
    edge_lookup.reserve(num_faces * 2);

    for (uint32_t face_index = 0; face_index < num_faces; ++face_index) {
        const SimpleFace &face = source.faces[face_index];
        face_edge_offsets[face_index] = static_cast<uint32_t>(corner_edges.size());
        ufbx_vec3 center = {0.0, 0.0, 0.0};
        for (uint32_t vertex : face.vertices) {
            center = add(center, source.vertices[vertex]);
            vertex_faces[vertex].push_back(face_index);
        }
        face_points[face_index] = divv(center, static_cast<double>(face.vertices.size()));

        for (size_t i = 0; i < face.vertices.size(); ++i) {
            uint32_t a = face.vertices[i];
            uint32_t b = face.vertices[(i + 1) % face.vertices.size()];
            uint64_t key = edge_key(a, b);
            auto inserted = edge_lookup.emplace(key, static_cast<uint32_t>(edges.size()));
            uint32_t edge_index = inserted.first->second;
            corner_edges.push_back(edge_index);
            if (inserted.second) {
                EdgeData edge;
                edge.a = std::min(a, b);
                edge.b = std::max(a, b);
                edges.push_back(std::move(edge));
                vertex_edges[a].push_back(edge_index);
                vertex_edges[b].push_back(edge_index);
            }
            auto &adjacent = edges[edge_index].faces;
            if (std::find(adjacent.begin(), adjacent.end(), face_index) == adjacent.end()) {
                adjacent.push_back(face_index);
            }
        }
    }

    SimpleMesh result;
    result.vertices.resize(num_vertices);
    size_t output_face_count = 0;
    for (const SimpleFace &face : source.faces) output_face_count += face.vertices.size();
    result.vertices.reserve(num_vertices + edges.size() + num_faces);
    result.faces.reserve(output_face_count);

    for (uint32_t vertex = 0; vertex < num_vertices; ++vertex) {
        const ufbx_vec3 p = source.vertices[vertex];
        std::vector<uint32_t> boundary_neighbors;
        bool non_manifold_boundary = false;
        for (uint32_t edge_index : vertex_edges[vertex]) {
            const EdgeData &edge = edges[edge_index];
            if (edge.faces.size() != 2) {
                if (edge.faces.size() > 1) non_manifold_boundary = true;
                uint32_t neighbor = edge.a == vertex ? edge.b : edge.a;
                if (std::find(boundary_neighbors.begin(), boundary_neighbors.end(), neighbor) == boundary_neighbors.end()) {
                    boundary_neighbors.push_back(neighbor);
                }
            }
        }

        if (!non_manifold_boundary && boundary_neighbors.size() == 2) {
            result.vertices[vertex] = divv(
                add(mul(p, 6.0), add(source.vertices[boundary_neighbors[0]], source.vertices[boundary_neighbors[1]])),
                8.0);
        } else if (!boundary_neighbors.empty() || vertex_faces[vertex].empty() || vertex_edges[vertex].empty()) {
            result.vertices[vertex] = p;
        } else {
            ufbx_vec3 f = {0.0, 0.0, 0.0};
            for (uint32_t face_index : vertex_faces[vertex]) f = add(f, face_points[face_index]);
            f = divv(f, static_cast<double>(vertex_faces[vertex].size()));

            ufbx_vec3 r = {0.0, 0.0, 0.0};
            for (uint32_t edge_index : vertex_edges[vertex]) {
                const EdgeData &edge = edges[edge_index];
                r = add(r, divv(add(source.vertices[edge.a], source.vertices[edge.b]), 2.0));
            }
            r = divv(r, static_cast<double>(vertex_edges[vertex].size()));
            double n = static_cast<double>(vertex_faces[vertex].size());
            result.vertices[vertex] = divv(add(add(f, mul(r, 2.0)), mul(p, n - 3.0)), n);
        }
    }

    for (EdgeData &edge : edges) {
        edge.point_index = static_cast<uint32_t>(result.vertices.size());
        ufbx_vec3 point;
        if (edge.faces.size() == 2) {
            point = divv(add(add(source.vertices[edge.a], source.vertices[edge.b]),
                             add(face_points[edge.faces[0]], face_points[edge.faces[1]])), 4.0);
        } else {
            point = divv(add(source.vertices[edge.a], source.vertices[edge.b]), 2.0);
        }
        result.vertices.push_back(point);
    }

    std::vector<uint32_t> face_point_indices(num_faces);
    for (uint32_t face_index = 0; face_index < num_faces; ++face_index) {
        face_point_indices[face_index] = static_cast<uint32_t>(result.vertices.size());
        result.vertices.push_back(face_points[face_index]);
    }

    for (uint32_t face_index = 0; face_index < num_faces; ++face_index) {
        const SimpleFace &face = source.faces[face_index];
        for (size_t i = 0; i < face.vertices.size(); ++i) {
            uint32_t current = face.vertices[i];
            SimpleFace quad;
            quad.material = face.material;
            quad.vertices = {
                current,
                edges[corner_edges[face_edge_offsets[face_index] + i]].point_index,
                face_point_indices[face_index],
                edges[corner_edges[face_edge_offsets[face_index] +
                    (i + face.vertices.size() - 1) % face.vertices.size()]].point_index,
            };
            result.faces.push_back(std::move(quad));
        }
    }
    return result;
}

std::vector<ufbx_vec3> compute_normals(const SimpleMesh &mesh)
{
    std::vector<ufbx_vec3> normals(mesh.vertices.size(), {0.0, 0.0, 0.0});
    for (const SimpleFace &face : mesh.faces) {
        ufbx_vec3 normal = {0.0, 0.0, 0.0};
        for (size_t i = 0; i < face.vertices.size(); ++i) {
            const ufbx_vec3 p = mesh.vertices[face.vertices[i]];
            const ufbx_vec3 q = mesh.vertices[face.vertices[(i + 1) % face.vertices.size()]];
            normal.x += (p.y - q.y) * (p.z + q.z);
            normal.y += (p.z - q.z) * (p.x + q.x);
            normal.z += (p.x - q.x) * (p.y + q.y);
        }
        for (uint32_t vertex : face.vertices) normals[vertex] = add(normals[vertex], normal);
    }
    for (ufbx_vec3 &normal : normals) normal = ufbx_vec3_normalize(normal);
    return normals;
}

std::string utf8_from_wide(const std::wstring &value)
{
#ifdef _WIN32
    if (value.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0) return {};
    std::string result(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size, nullptr, nullptr);
    return result;
#else
    return value.empty() ? std::string() : fs::path(value).string();
#endif
}

std::string string_from_ufbx(ufbx_string value)
{
    return value.data ? std::string(value.data, value.length) : std::string();
}

std::string safe_obj_name(std::string name, const char *fallback)
{
    if (name.empty()) name = fallback;
    for (char &ch : name) {
        if (ch == '\r' || ch == '\n' || ch == '\0') ch = '_';
    }
    return name;
}

bool parse_u32(const std::wstring &text, uint32_t &value)
{
    try {
        size_t pos = 0;
        unsigned long parsed = std::stoul(text, &pos, 10);
        if (pos != text.size() || parsed > std::numeric_limits<uint32_t>::max()) return false;
        value = static_cast<uint32_t>(parsed);
        return true;
    } catch (...) {
        return false;
    }
}

bool parse_u64(const std::wstring &text, uint64_t &value)
{
    try {
        size_t pos = 0;
        unsigned long long parsed = std::stoull(text, &pos, 10);
        if (pos != text.size()) return false;
        value = static_cast<uint64_t>(parsed);
        return true;
    } catch (...) {
        return false;
    }
}

bool parse_arguments(int argc, wchar_t **argv, Arguments &args, std::string &error)
{
    for (int i = 1; i < argc; ++i) {
        std::wstring arg = argv[i];
        auto require_value = [&](const char *name) -> const wchar_t * {
            if (i + 1 >= argc) {
                error = std::string("Missing value for ") + name;
                return nullptr;
            }
            return argv[++i];
        };

        if (arg == L"--input") {
            const wchar_t *value = require_value("--input");
            if (!value) return false;
            args.input = value;
        } else if (arg == L"--output") {
            const wchar_t *value = require_value("--output");
            if (!value) return false;
            args.output = value;
        } else if (arg == L"--levels-file") {
            const wchar_t *value = require_value("--levels-file");
            if (!value) return false;
            args.levels_file = value;
        } else if (arg == L"--input-binary") {
            args.binary_input = true;
        } else if (arg == L"--format") {
            const wchar_t *value = require_value("--format");
            if (!value) return false;
            if (std::wcscmp(value, L"fbx") == 0) args.output_fbx = true;
            else if (std::wcscmp(value, L"obj") == 0) args.output_fbx = false;
            else { error = "--format must be fbx or obj"; return false; }
        } else if (arg == L"--level") {
            const wchar_t *value = require_value("--level");
            if (!value || !parse_u32(value, args.level)) {
                error = "Invalid --level value";
                return false;
            }
        } else if (arg == L"--max-faces") {
            const wchar_t *value = require_value("--max-faces");
            if (!value || !parse_u64(value, args.max_faces)) {
                error = "Invalid --max-faces value";
                return false;
            }
        } else if (arg == L"--help" || arg == L"-h") {
            args.show_help = true;
        } else if (arg == L"--version") {
            args.show_version = true;
        } else {
            error = "Unknown argument: " + utf8_from_wide(arg);
            return false;
        }
    }

    if (args.show_help || args.show_version) return true;
    if (args.input.empty()) {
        error = "--input is required";
        return false;
    }
    if (args.output.empty()) {
        error = "--output is required";
        return false;
    }
    if (args.level > 5) {
        error = "--level must be between 0 and 5";
        return false;
    }
    if (args.max_faces == 0) {
        error = "--max-faces must be greater than zero";
        return false;
    }
    if (args.binary_input && !args.levels_file.empty()) {
        error = "--levels-file is not used with --input-binary";
        return false;
    }
    if (args.output_fbx && !args.binary_input) {
        error = "--format fbx currently requires --input-binary";
        return false;
    }
    return true;
}

uint64_t estimate_subdivided_faces(const ufbx_mesh *mesh, uint32_t level)
{
    uint64_t faces = 0;
    for (size_t i = 0; i < mesh->faces.count; ++i) {
        uint32_t corners = mesh->faces.data[i].num_indices;
        if (corners >= 3) {
            if (faces > std::numeric_limits<uint64_t>::max() - corners) return std::numeric_limits<uint64_t>::max();
            faces += corners;
        }
    }
    for (uint32_t i = 1; i < level; ++i) {
        if (faces > std::numeric_limits<uint64_t>::max() / 4) return std::numeric_limits<uint64_t>::max();
        faces *= 4;
    }
    return faces;
}

double matrix_determinant3(const ufbx_matrix &m)
{
    return m.m00 * (m.m11 * m.m22 - m.m12 * m.m21)
         - m.m01 * (m.m10 * m.m22 - m.m12 * m.m20)
         + m.m02 * (m.m10 * m.m21 - m.m11 * m.m20);
}

std::string unique_material_name(const ufbx_material *material, std::set<std::string> &used)
{
    std::string base = material ? safe_obj_name(string_from_ufbx(material->name), "Material") : "Material";
    std::string candidate = base;
    uint32_t suffix = 2;
    while (!used.insert(candidate).second) {
        candidate = base + "_" + std::to_string(suffix++);
    }
    return candidate;
}

ufbx_vec3 material_color(const ufbx_material *material)
{
    if (!material) return {0.8, 0.8, 0.8};
    const ufbx_material_map &pbr = material->pbr.base_color;
    if (pbr.has_value && pbr.value_components >= 3) return pbr.value_vec3;
    const ufbx_material_map &fbx = material->fbx.diffuse_color;
    if (fbx.has_value && fbx.value_components >= 3) return fbx.value_vec3;
    return {0.8, 0.8, 0.8};
}

bool write_mtl(const fs::path &path, const std::map<const ufbx_material *, MaterialInfo> &materials, std::string &error)
{
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        error = "Could not create MTL: " + utf8_from_wide(path.wstring());
        return false;
    }
    out << "# Generated by Bake Groups OBJ Subdivider\n";
    out << std::setprecision(9);
    for (const auto &entry : materials) {
        const MaterialInfo &info = entry.second;
        out << "\nnewmtl " << info.name << "\n";
        out << "Ka 0 0 0\n";
        out << "Kd " << info.color.x << ' ' << info.color.y << ' ' << info.color.z << "\n";
        out << "Ks 0 0 0\n";
        out << "d 1\nillum 1\n";
    }
    if (!out.good()) {
        error = "Failed while writing MTL";
        return false;
    }
    return true;
}

class BinaryReader {
public:
    explicit BinaryReader(const fs::path &path) : input_(path, std::ios::binary) {}
    bool good() const { return input_.good(); }
    void bytes(char *target, size_t count)
    {
        if (count > static_cast<size_t>(std::numeric_limits<std::streamsize>::max()) ||
            !input_.read(target, static_cast<std::streamsize>(count))) {
            throw std::runtime_error("Truncated HP geometry stream");
        }
    }
    uint32_t u32()
    {
        unsigned char data[4];
        bytes(reinterpret_cast<char *>(data), 4);
        return uint32_t(data[0]) | (uint32_t(data[1]) << 8) |
               (uint32_t(data[2]) << 16) | (uint32_t(data[3]) << 24);
    }
    uint64_t u64()
    {
        uint64_t low = u32();
        uint64_t high = u32();
        return low | (high << 32);
    }
    double f64()
    {
        const uint64_t bits = u64();
        double value;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }
    ufbx_vec3 point() { return {f64(), f64(), f64()}; }
    std::string string()
    {
        const uint32_t length = u32();
        if (length > 1024 * 1024) throw std::runtime_error("HP geometry name is too long");
        std::string result(length, '\0');
        if (length) bytes(result.data(), length);
        return result;
    }
    bool at_end() { return input_.peek() == std::char_traits<char>::eof(); }
private:
    std::ifstream input_;
};

bool write_binary_mtl(const fs::path &path,
                      const std::map<std::string, ufbx_vec3> &materials,
                      std::string &error)
{
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        error = "Could not create MTL: " + utf8_from_wide(path.wstring());
        return false;
    }
    out << "# Generated by Bake Groups OBJ Subdivider\n" << std::setprecision(9);
    for (const auto &entry : materials) {
        out << "\nnewmtl " << entry.first << "\nKa 0 0 0\nKd "
            << entry.second.x << ' ' << entry.second.y << ' ' << entry.second.z
            << "\nKs 0 0 0\nd 1\nillum 1\n";
    }
    if (!out.good()) {
        error = "Failed while writing MTL";
        return false;
    }
    return true;
}

using FbxScene = std::unique_ptr<ufbxw_scene, decltype(&ufbxw_free_scene)>;

bool write_fbx_chunk(void *user, uint64_t offset, const void *data, size_t size)
{
    auto &stream = *static_cast<std::fstream *>(user);
    if (offset > static_cast<uint64_t>(std::numeric_limits<std::streamoff>::max()) ||
        size > static_cast<size_t>(std::numeric_limits<std::streamsize>::max())) return false;
    stream.seekp(static_cast<std::streamoff>(offset));
    stream.write(static_cast<const char *>(data), static_cast<std::streamsize>(size));
    return stream.good();
}

void append_fbx_mesh(ufbxw_scene *scene, const std::string &name,
                     const SimpleMesh &mesh, const std::vector<ufbx_vec3> &source_normals,
                     const std::vector<std::vector<uint32_t>> &normal_ids,
                     const std::vector<std::string> &material_names,
                     const std::map<std::string, ufbx_vec3> &all_materials,
                     std::map<std::string, ufbxw_material> &fbx_materials)
{
    if (mesh.vertices.size() > INT32_MAX || mesh.faces.size() > INT32_MAX)
        throw std::runtime_error("FBX mesh exceeds 32-bit geometry limit: " + name);
    size_t corner_count = 0;
    for (const SimpleFace &face : mesh.faces) corner_count += face.vertices.size();
    if (corner_count > INT32_MAX) throw std::runtime_error("FBX mesh has too many corners: " + name);

    const ufbxw_node node = ufbxw_create_node(scene);
    ufbxw_set_name(scene, node.id, name.c_str());
    const ufbxw_mesh target = ufbxw_create_mesh(scene);
    ufbxw_set_name(scene, target.id, name.c_str());
    ufbxw_mesh_add_instance(scene, target, node);

    std::vector<ufbxw_vec3> vertices;
    vertices.reserve(mesh.vertices.size());
    for (ufbx_vec3 p : mesh.vertices) vertices.push_back({p.x, p.y, p.z});
    ufbxw_mesh_set_vertices(scene, target, ufbxw_copy_vec3_array(scene, vertices.data(), vertices.size()));

    std::vector<int32_t> indices, offsets, face_materials;
    indices.reserve(corner_count);
    offsets.reserve(mesh.faces.size() + 1);
    face_materials.reserve(mesh.faces.size());
    offsets.push_back(0);
    bool has_material = false;
    for (const SimpleFace &face : mesh.faces) {
        for (uint32_t index : face.vertices) indices.push_back(static_cast<int32_t>(index));
        offsets.push_back(static_cast<int32_t>(indices.size()));
        face_materials.push_back(face.material == UFBX_NO_INDEX ? 0 : static_cast<int32_t>(face.material));
        has_material |= face.material != UFBX_NO_INDEX;
    }
    ufbxw_mesh_set_polygons(scene, target,
        ufbxw_copy_int_array(scene, indices.data(), indices.size()),
        ufbxw_copy_int_array(scene, offsets.data(), offsets.size()));

    if (!source_normals.empty()) {
        std::vector<ufbxw_vec3> normals;
        normals.reserve(corner_count);
        for (size_t face = 0; face < mesh.faces.size(); ++face) {
            for (uint32_t normal_id : normal_ids[face]) {
                ufbx_vec3 n = source_normals[normal_id];
                normals.push_back({n.x, n.y, n.z});
            }
        }
        ufbxw_mesh_set_normals(scene, target,
            ufbxw_copy_vec3_array(scene, normals.data(), normals.size()),
            UFBXW_ATTRIBUTE_MAPPING_POLYGON_VERTEX);
    } else {
        std::vector<ufbx_vec3> generated = compute_normals(mesh);
        std::vector<ufbxw_vec3> normals;
        normals.reserve(generated.size());
        for (ufbx_vec3 n : generated) normals.push_back({n.x, n.y, n.z});
        ufbxw_mesh_set_normals(scene, target,
            ufbxw_copy_vec3_array(scene, normals.data(), normals.size()),
            UFBXW_ATTRIBUTE_MAPPING_VERTEX);
    }

    for (size_t i = 0; i < material_names.size(); ++i) {
        const std::string &material_name = material_names[i];
        auto it = fbx_materials.find(material_name);
        if (it == fbx_materials.end()) {
            ufbxw_material material = ufbxw_create_material(scene, UFBXW_MATERIAL_FBX_LAMBERT);
            ufbxw_set_name(scene, material.id, material_name.c_str());
            const ufbx_vec3 color = all_materials.at(material_name);
            ufbxw_set_vec3(scene, material.id, "DiffuseColor", {color.x, color.y, color.z});
            it = fbx_materials.emplace(material_name, material).first;
        }
        ufbxw_node_set_material(scene, node, i, it->second);
    }
    if (has_material && !material_names.empty()) {
        ufbxw_mesh_set_face_material(scene, target,
            ufbxw_copy_int_array(scene, face_materials.data(), face_materials.size()));
    }
}

bool convert_binary(const Arguments &args, std::string &error)
{
    BinaryReader input(args.input);
    if (!input.good()) {
        error = "Could not open HP geometry stream: " + utf8_from_wide(args.input.wstring());
        return false;
    }
    try {
        char magic[8];
        input.bytes(magic, sizeof(magic));
        if (std::memcmp(magic, "BGHPBIN1", 8) != 0) {
            throw std::runtime_error("Unsupported HP geometry stream version");
        }
        const uint32_t mesh_count = input.u32();
        if (mesh_count == 0 || mesh_count > 1'000'000) {
            throw std::runtime_error("Invalid HP mesh count");
        }
        fs::create_directories(args.output.parent_path());
        fs::path mtl_path = args.output;
        mtl_path.replace_extension(L".mtl");
        std::ofstream out;
        std::unique_ptr<FastObjWriter> writer;
        FbxScene scene(nullptr, &ufbxw_free_scene);
        std::map<std::string, ufbxw_material> fbx_materials;
        if (args.output_fbx) {
            scene.reset(ufbxw_create_scene(nullptr));
            if (!scene) throw std::runtime_error("Could not create FBX scene");
        } else {
            out.open(args.output, std::ios::binary);
            if (!out) throw std::runtime_error("Could not create OBJ output");
            writer = std::make_unique<FastObjWriter>(out);
            writer->append_text("# Generated by Bake Groups OBJ Subdivider\nmtllib ");
            writer->append_text(utf8_from_wide(mtl_path.filename().wstring()));
            writer->append_char('\n');
        }

        std::map<std::string, ufbx_vec3> all_materials;
        uint64_t vertex_offset = 0, normal_offset = 0, estimated_faces = 0, actual_faces = 0;
        for (uint32_t mesh_index = 0; mesh_index < mesh_count; ++mesh_index) {
            const std::string name = safe_obj_name(input.string(), "Mesh");
            const uint32_t level = input.u32();
            if (level > 5) throw std::runtime_error("Invalid HP Smooth level: " + name);
            const uint32_t material_count = input.u32();
            if (material_count > 65536) throw std::runtime_error("Too many HP materials");
            std::vector<std::string> material_names;
            material_names.reserve(material_count);
            for (uint32_t i = 0; i < material_count; ++i) {
                std::string material_name = safe_obj_name(input.string(), "Material");
                ufbx_vec3 color = input.point();
                auto result = all_materials.emplace(material_name, color);
                if (!result.second &&
                    (result.first->second.x != color.x || result.first->second.y != color.y ||
                     result.first->second.z != color.z)) {
                    throw std::runtime_error("Conflicting HP material name: " + material_name);
                }
                material_names.push_back(std::move(material_name));
            }
            const uint32_t vertex_count = input.u32();
            const uint32_t face_count = input.u32();
            const uint64_t corner_count = input.u64();
            const uint32_t normal_count = input.u32();
            if (!vertex_count || !face_count || corner_count < uint64_t(face_count) * 3 ||
                corner_count > 200'000'000 || normal_count > 200'000'000 ||
                (level > 0 && normal_count != 0)) {
                throw std::runtime_error("Invalid HP geometry counts: " + name);
            }
            SimpleMesh mesh;
            mesh.vertices.resize(vertex_count);
            for (ufbx_vec3 &point : mesh.vertices) point = input.point();
            std::vector<ufbx_vec3> source_normals(normal_count);
            for (ufbx_vec3 &normal : source_normals) normal = input.point();
            mesh.faces.reserve(face_count);
            std::vector<std::vector<uint32_t>> normal_ids;
            if (normal_count) normal_ids.reserve(face_count);
            uint64_t read_corners = 0, next_faces = 0;
            for (uint32_t face_index = 0; face_index < face_count; ++face_index) {
                const uint32_t corners = input.u32();
                const uint32_t material = input.u32();
                if (corners < 3 || corners > corner_count - read_corners ||
                    (material != UFBX_NO_INDEX && material >= material_count)) {
                    throw std::runtime_error("Invalid HP face: " + name);
                }
                SimpleFace face;
                face.material = material;
                face.vertices.resize(corners);
                for (uint32_t &index : face.vertices) {
                    index = input.u32();
                    if (index >= vertex_count) throw std::runtime_error("Invalid HP vertex index: " + name);
                }
                if (normal_count) {
                    normal_ids.emplace_back(corners);
                    for (uint32_t &index : normal_ids.back()) {
                        index = input.u32();
                        if (index >= normal_count) throw std::runtime_error("Invalid HP normal index: " + name);
                    }
                }
                read_corners += corners;
                next_faces += level ? corners : 1;
                mesh.faces.push_back(std::move(face));
            }
            if (read_corners != corner_count) throw std::runtime_error("HP corner count mismatch: " + name);
            for (uint32_t i = 1; i < level; ++i) {
                if (next_faces > args.max_faces / 4) throw std::runtime_error("HP subdivision exceeds face limit");
                next_faces *= 4;
            }
            if (estimated_faces > args.max_faces || next_faces > args.max_faces - estimated_faces) {
                throw std::runtime_error("HP subdivision exceeds face limit");
            }
            estimated_faces += next_faces;
            std::cout << "Subdividing [" << mesh_index + 1 << '/' << mesh_count << "]: "
                      << name << " (" << vertex_count << " vertices, " << face_count
                      << " faces, level " << level << ")" << std::endl;
            for (uint32_t iteration = 0; iteration < level; ++iteration) mesh = subdivide_once(mesh);

            if (args.output_fbx) {
                append_fbx_mesh(scene.get(), name, mesh, source_normals,
                                normal_ids, material_names, all_materials, fbx_materials);
                actual_faces += mesh.faces.size();
                vertex_offset += mesh.vertices.size();
                continue;
            }

            writer->append_text("\no ");
            writer->append_text(name);
            writer->append_char('\n');
            for (const ufbx_vec3 point : mesh.vertices) {
                writer->append_text("v ");
                writer->append_double(point.x); writer->append_char(' ');
                writer->append_double(point.y); writer->append_char(' ');
                writer->append_double(point.z); writer->append_char('\n');
            }
            const std::vector<ufbx_vec3> output_normals = normal_count ?
                std::move(source_normals) : compute_normals(mesh);
            for (const ufbx_vec3 normal : output_normals) {
                writer->append_text("vn ");
                writer->append_double(normal.x); writer->append_char(' ');
                writer->append_double(normal.y); writer->append_char(' ');
                writer->append_double(normal.z); writer->append_char('\n');
            }
            uint32_t active_material = UFBX_NO_INDEX;
            bool material_initialized = false;
            for (size_t face_index = 0; face_index < mesh.faces.size(); ++face_index) {
                const SimpleFace &face = mesh.faces[face_index];
                if (!material_initialized || active_material != face.material) {
                    active_material = face.material;
                    material_initialized = true;
                    if (active_material != UFBX_NO_INDEX) {
                        writer->append_text("usemtl ");
                        writer->append_text(material_names[active_material]);
                        writer->append_char('\n');
                    }
                }
                writer->append_char('f');
                for (size_t corner = 0; corner < face.vertices.size(); ++corner) {
                    writer->append_char(' ');
                    writer->append_index(vertex_offset + face.vertices[corner] + 1);
                    writer->append_text("//");
                    const uint32_t normal_id = normal_count ?
                        normal_ids[face_index][corner] : face.vertices[corner];
                    writer->append_index(normal_offset + normal_id + 1);
                }
                writer->append_char('\n');
                ++actual_faces;
            }
            vertex_offset += mesh.vertices.size();
            normal_offset += output_normals.size();
            if (!writer->good()) throw std::runtime_error("Failed while writing OBJ");
        }
        if (!input.at_end()) throw std::runtime_error("Trailing data in HP geometry stream");
        if (args.output_fbx) {
            ufbxw_prepare_scene(scene.get(), &ufbxw_default_prepare_opts);
            ufbxw_save_opts options = {};
            options.format = UFBXW_SAVE_FORMAT_BINARY;
            options.version = 7500;
            ufbxw_error save_error = {};
            std::fstream fbx_output(args.output,
                                    std::ios::in | std::ios::out | std::ios::binary | std::ios::trunc);
            if (!fbx_output) throw std::runtime_error("Could not create FBX output");
            ufbxw_write_stream stream = {};
            stream.write_fn = &write_fbx_chunk;
            stream.user = &fbx_output;
            if (!ufbxw_save_stream(scene.get(), &stream, &options, &save_error)) {
                throw std::runtime_error(std::string("Could not write FBX: ") + save_error.description);
            }
            fbx_output.flush();
            if (!fbx_output.good()) throw std::runtime_error("Could not flush FBX output");
        } else {
            if (!writer->flush()) throw std::runtime_error("Failed to finalize OBJ");
            out.close();
            if (!out.good()) throw std::runtime_error("Failed to close OBJ");
            if (!write_binary_mtl(mtl_path, all_materials, error)) return false;
        }
        std::cout << "Done: " << actual_faces << " polygons, " << vertex_offset << " vertices" << std::endl;
        return true;
    } catch (const std::exception &exc) {
        error = exc.what();
        return false;
    }
}

bool convert(const Arguments &args, std::string &error)
{
    if (args.binary_input) return convert_binary(args, error);
    if (!fs::exists(args.input)) {
        error = "Input FBX does not exist: " + utf8_from_wide(args.input.wstring());
        return false;
    }

    std::unordered_map<std::string, uint32_t> mesh_levels;
    if (!args.levels_file.empty()) {
        std::ifstream levels(args.levels_file, std::ios::binary);
        if (!levels) {
            error = "Could not open --levels-file: " + utf8_from_wide(args.levels_file.wstring());
            return false;
        }
        std::string line;
        size_t line_number = 0;
        while (std::getline(levels, line)) {
            ++line_number;
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.empty()) continue;
            const size_t separator = line.find('\t');
            if (separator == std::string::npos || separator == 0 || separator + 1 >= line.size()) {
                error = "Invalid levels-file row " + std::to_string(line_number) + " (expected level<TAB>mesh name)";
                return false;
            }
            uint32_t level = 0;
            const char *begin = line.data();
            const char *end = begin + separator;
            const auto parsed = std::from_chars(begin, end, level);
            if (parsed.ec != std::errc() || parsed.ptr != end || level > 5) {
                error = "Invalid subdivision level on levels-file row " + std::to_string(line_number);
                return false;
            }
            const std::string name = line.substr(separator + 1);
            if (!mesh_levels.emplace(name, level).second) {
                error = "Duplicate mesh name in levels file: " + name;
                return false;
            }
        }
        if (levels.bad()) {
            error = "Failed while reading --levels-file";
            return false;
        }
    }

    fs::create_directories(args.output.parent_path());

    ufbx_load_opts load_opts = {};
    // OBJ normals are generated from the final subdivided mesh below.
    load_opts.generate_missing_normals = false;
    load_opts.load_external_files = false;
    load_opts.evaluate_skinning = true;
    load_opts.evaluate_caches = false;

    ufbx_error load_error = {};
    std::string input_utf8 = utf8_from_wide(args.input.wstring());
    ufbx_scene *scene = ufbx_load_file(input_utf8.c_str(), &load_opts, &load_error);
    if (!scene) {
        error = "FBX load failed: " + string_from_ufbx(load_error.description);
        return false;
    }

    uint64_t estimated_faces = 0;
    size_t mesh_nodes = 0;
    std::set<std::string> matched_mesh_names;
    for (size_t i = 0; i < scene->nodes.count; ++i) {
        const ufbx_node *node = scene->nodes.data[i];
        if (!node->mesh || node->is_root) continue;
        const std::string node_name = string_from_ufbx(node->name);
        auto level_it = mesh_levels.find(node_name);
        const uint32_t node_level = level_it != mesh_levels.end() ? level_it->second : args.level;
        if (level_it != mesh_levels.end()) matched_mesh_names.insert(node_name);
        uint64_t node_faces = estimate_subdivided_faces(node->mesh, node_level);
        if (estimated_faces > args.max_faces || node_faces > args.max_faces - estimated_faces) {
            ufbx_free_scene(scene);
            std::ostringstream ss;
            ss << "Subdivision would exceed the safety limit of " << args.max_faces
               << " faces. Lower the level or raise the limit.";
            error = ss.str();
            return false;
        }
        estimated_faces += node_faces;
        ++mesh_nodes;
    }
    if (matched_mesh_names.size() != mesh_levels.size()) {
        for (const auto &entry : mesh_levels) {
            if (matched_mesh_names.find(entry.first) == matched_mesh_names.end()) {
                ufbx_free_scene(scene);
                error = "Mesh name from levels file was not found in FBX: " + entry.first;
                return false;
            }
        }
    }
    if (mesh_nodes == 0) {
        ufbx_free_scene(scene);
        error = "The FBX contains no polygon meshes";
        return false;
    }

    std::cout << "Loaded " << mesh_nodes << " mesh object(s); estimated output: "
              << estimated_faces << " polygons" << std::endl;

    fs::path mtl_path = args.output;
    mtl_path.replace_extension(L".mtl");
    std::ofstream out(args.output, std::ios::binary);
    if (!out) {
        ufbx_free_scene(scene);
        error = "Could not create OBJ: " + utf8_from_wide(args.output.wstring());
        return false;
    }
    FastObjWriter writer(out);
    writer.append_text("# Generated by Bake Groups OBJ Subdivider\nmtllib ");
    writer.append_text(utf8_from_wide(mtl_path.filename().wstring()));
    writer.append_char('\n');

    uint64_t vertex_offset = 0;
    uint64_t normal_offset = 0;
    uint64_t actual_faces = 0;
    size_t processed_nodes = 0;
    std::map<const ufbx_material *, MaterialInfo> materials;
    std::set<std::string> material_names;

    for (size_t node_index = 0; node_index < scene->nodes.count; ++node_index) {
        const ufbx_node *node = scene->nodes.data[node_index];
        if (!node->mesh || node->is_root) continue;

        ++processed_nodes;
        const std::string node_name = string_from_ufbx(node->name);
        auto level_it = mesh_levels.find(node_name);
        const uint32_t node_level = level_it != mesh_levels.end() ? level_it->second : args.level;
        std::string object_name = safe_obj_name(node_name, "Mesh");
        std::cout << "Subdividing [" << processed_nodes << '/' << mesh_nodes << "]: "
                  << object_name << " (" << node->mesh->num_vertices << " vertices, "
                  << node->mesh->num_faces << " faces, level " << node_level << ")" << std::endl;

        SimpleMesh mesh;
        size_t skipped_faces = 0;
        std::string mesh_error;
        if (!make_simple_mesh(node->mesh, mesh, skipped_faces, mesh_error)) {
            writer.flush();
            out.close();
            ufbx_free_scene(scene);
            error = "Invalid geometry in '" + object_name + "': " + mesh_error;
            return false;
        }
        if (skipped_faces > 0) {
            std::cout << "Skipped " << skipped_faces << " degenerate face(s) in " << object_name << std::endl;
        }
        for (uint32_t level = 0; level < node_level; ++level) {
            mesh = subdivide_once(mesh);
        }
        writer.append_text("\no ");
        writer.append_text(object_name);
        writer.append_char('\n');

        for (const ufbx_vec3 local_position : mesh.vertices) {
            ufbx_vec3 p = ufbx_transform_position(&node->geometry_to_world, local_position);
            writer.append_text("v ");
            writer.append_double(p.x);
            writer.append_char(' ');
            writer.append_double(p.y);
            writer.append_char(' ');
            writer.append_double(p.z);
            writer.append_char('\n');
        }

        const std::vector<ufbx_vec3> local_normals = compute_normals(mesh);
        ufbx_matrix normal_matrix = ufbx_matrix_for_normals(&node->geometry_to_world);
        for (const ufbx_vec3 local_normal : local_normals) {
            ufbx_vec3 n = ufbx_transform_direction(&normal_matrix, local_normal);
            n = ufbx_vec3_normalize(n);
            writer.append_text("vn ");
            writer.append_double(n.x);
            writer.append_char(' ');
            writer.append_double(n.y);
            writer.append_char(' ');
            writer.append_double(n.z);
            writer.append_char('\n');
        }

        bool reverse = matrix_determinant3(node->geometry_to_world) < 0.0;
        const ufbx_material *active_material = reinterpret_cast<const ufbx_material *>(uintptr_t(1));

        for (const SimpleFace &face : mesh.faces) {
            const ufbx_material *material = nullptr;
            if (face.material < node->materials.count) {
                material = node->materials.data[face.material];
            }
            if (material != active_material) {
                active_material = material;
                if (material) {
                    auto it = materials.find(material);
                    if (it == materials.end()) {
                        MaterialInfo info;
                        info.name = unique_material_name(material, material_names);
                        info.color = material_color(material);
                        it = materials.emplace(material, std::move(info)).first;
                    }
                    writer.append_text("usemtl ");
                    writer.append_text(it->second.name);
                    writer.append_char('\n');
                }
            }

            writer.append_char('f');
            for (size_t corner_number = 0; corner_number < face.vertices.size(); ++corner_number) {
                size_t local_corner = reverse ? (face.vertices.size() - 1 - corner_number) : corner_number;
                uint32_t local_vertex = face.vertices[local_corner];
                uint64_t vertex = vertex_offset + local_vertex + 1;
                uint64_t normal = normal_offset + local_vertex + 1;
                writer.append_char(' ');
                writer.append_index(vertex);
                writer.append_text("//");
                writer.append_index(normal);
            }
            writer.append_char('\n');
            ++actual_faces;
        }

        vertex_offset += mesh.vertices.size();
        normal_offset += local_normals.size();

        if (!writer.good()) {
            writer.flush();
            ufbx_free_scene(scene);
            error = "Failed while writing OBJ";
            return false;
        }
    }

    writer.flush();
    out.close();
    if (!out.good()) {
        ufbx_free_scene(scene);
        error = "Failed to finalize OBJ";
        return false;
    }

    if (!write_mtl(mtl_path, materials, error)) {
        ufbx_free_scene(scene);
        return false;
    }

    ufbx_free_scene(scene);
    std::cout << "Done: " << actual_faces << " polygons, " << vertex_offset << " vertices" << std::endl;
    return true;
}

void print_help()
{
    std::cout
        << "Bake Groups HP Subdivider 0.4.0\n"
        << "Usage:\n"
        << "  bg_obj_subdivider.exe --input source.bghp --input-binary --output result.fbx --format fbx\n"
        << "  bg_obj_subdivider.exe --input source.fbx --output result.obj --level 0\n\n"
        << "Options:\n"
        << "  --level N       Default Catmull-Clark iterations (0..5)\n"
        << "  --input-binary  Read UV-free Maya HP geometry stream\n"
        << "  --format fbx|obj  Output format (FBX requires --input-binary; default OBJ)\n"
        << "  --levels-file   UTF-8 TSV: one row per mesh, level<TAB>mesh name\n"
        << "  --max-faces N   Output safety limit (default 50000000)\n";
}

} // namespace

int wmain(int argc, wchar_t **argv)
{
    Arguments args;
    std::string error;
    if (!parse_arguments(argc, argv, args, error)) {
        std::cerr << "Error: " << error << std::endl;
        print_help();
        return 2;
    }
    if (args.show_version) {
        std::cout << "0.4.0" << std::endl;
        return 0;
    }
    if (args.show_help) {
        print_help();
        return 0;
    }
    if (!convert(args, error)) {
        std::cerr << "Error: " << error << std::endl;
        return 1;
    }
    return 0;
}
