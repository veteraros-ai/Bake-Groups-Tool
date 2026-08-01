#include <pybind11/pybind11.h>
#include <pybind11/stl.h> // Автоматически конвертирует tuple/list в std::vector
#include <vector>
#include <cmath>
#include <thread>
#include <limits>
#include <algorithm>
#include <string>
#include <sstream>
#include <iomanip>
#include <unordered_map>
#include <numeric>
#include <tuple>
#include <utility>

namespace py = pybind11;

// ============================================================================
// 0. СТРУКТУРЫ ДАННЫХ
// ============================================================================

struct MeshMetrics {
    float elongation;
    float symmetry_score;
    std::vector<float> dimensions;
    std::vector<float> center;
};

struct PosHash {
    size_t operator()(const std::tuple<int, int, int>& v) const {
        return std::get<0>(v) * 73856093 ^ std::get<1>(v) * 19349663 ^ std::get<2>(v) * 83492791;
    }
};

// ============================================================================
// 1. АНАЛИЗ ДИСТАНЦИЙ (SPATIAL GRID + МНОГОПОТОЧНОСТЬ)
// ============================================================================

// Порог, ниже которого перебор дешевле, чем построение сетки.
static const size_t BRUTE_FORCE_REF_MAX = 64;
// Порог числа запросов, ниже которого не плодим потоки.
static const size_t MIN_PARALLEL_QUERIES = 512;

// Перебор ближайшей вершины (квадрат дистанции) для маленьких облаков.
static inline float brute_nearest_sq(const std::vector<float>& ref, float px, float py, float pz, float best_sq) {
    size_t c = ref.size() / 3;
    for (size_t j = 0; j < c; ++j) {
        float dx = px - ref[j * 3];
        float dy = py - ref[j * 3 + 1];
        float dz = pz - ref[j * 3 + 2];
        float d = dx * dx + dy * dy + dz * dz;
        if (d < best_sq) best_sq = d;
    }
    return best_sq;
}

// Равномерная пространственная сетка над облаком точек.
// Запрос ближайшего соседа — расширяющиеся оболочки (shells) вокруг ячейки запроса
// с точной ранней остановкой: превращает O(N*M) перебор в ~O(N+M).
struct SpatialGrid {
    const std::vector<float>& pts;
    float cell_size;
    float inv_cell;
    float ox, oy, oz;                       // угол-начало (минимум bbox)
    int gminx, gminy, gminz;                // границы занятых ячеек (индексы)
    int gmaxx, gmaxy, gmaxz;
    int max_r;                              // радиус, покрывающий всю сетку
    std::unordered_map<std::tuple<int, int, int>, std::vector<int>, PosHash> cells;

    static inline int clampi(int v, int lo, int hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }

    explicit SpatialGrid(const std::vector<float>& p) : pts(p) {
        size_t n = p.size() / 3;
        float minx = p[0], miny = p[1], minz = p[2];
        float maxx = p[0], maxy = p[1], maxz = p[2];
        for (size_t i = 0; i < p.size(); i += 3) {
            minx = std::min(minx, p[i]);     maxx = std::max(maxx, p[i]);
            miny = std::min(miny, p[i + 1]); maxy = std::max(maxy, p[i + 1]);
            minz = std::min(minz, p[i + 2]); maxz = std::max(maxz, p[i + 2]);
        }
        float ex = maxx - minx, ey = maxy - miny, ez = maxz - minz;
        float diag = std::sqrt(ex * ex + ey * ey + ez * ez);
        float cbrtn = std::cbrt(static_cast<float>(std::max<size_t>(n, 1)));
        cell_size = diag / std::max(1.0f, cbrtn);   // ~1 точка на ячейку
        if (!(cell_size > 1e-6f)) cell_size = 1e-6f; // вырожденный случай / NaN
        inv_cell = 1.0f / cell_size;
        ox = minx; oy = miny; oz = minz;

        cells.reserve(n);
        gminx = gminy = gminz = std::numeric_limits<int>::max();
        gmaxx = gmaxy = gmaxz = std::numeric_limits<int>::min();
        for (size_t i = 0; i < n; ++i) {
            int gx = static_cast<int>(std::floor((p[i * 3]     - ox) * inv_cell));
            int gy = static_cast<int>(std::floor((p[i * 3 + 1] - oy) * inv_cell));
            int gz = static_cast<int>(std::floor((p[i * 3 + 2] - oz) * inv_cell));
            cells[std::make_tuple(gx, gy, gz)].push_back(static_cast<int>(i));
            gminx = std::min(gminx, gx); gmaxx = std::max(gmaxx, gx);
            gminy = std::min(gminy, gy); gmaxy = std::max(gmaxy, gy);
            gminz = std::min(gminz, gz); gmaxz = std::max(gmaxz, gz);
        }
        // Оболочки центрируются на занятой ячейке; этого радиуса хватает,
        // чтобы из любой граничной ячейки достать все остальные.
        max_r = std::max(gmaxx - gminx, std::max(gmaxy - gminy, gmaxz - gminz)) + 1;
    }

    // Дистанция от q до отрезка [lo, hi] (0, если внутри).
    static inline float interval_dist(float q, float lo, float hi) {
        if (q < lo) return lo - q;
        if (q > hi) return q - hi;
        return 0.0f;
    }

    // Дистанция от q до непросмотренной части сетки на одной оси:
    // два отрезка [gm_lo, box_lo) и [box_hi, gm_hi) (то, что внутри bbox сетки,
    // но снаружи просканированного бокса). FLT_MAX, если таких точек на оси нет.
    static inline float unscanned_dist(float q, float gm_lo, float gm_hi, float box_lo, float box_hi) {
        float best = std::numeric_limits<float>::max();
        if (box_lo > gm_lo) {                       // левый отрезок непуст
            if (q <= gm_lo) best = gm_lo - q;
            else if (q >= box_lo) best = q - box_lo;
            else best = 0.0f;
        }
        if (box_hi < gm_hi) {                       // правый отрезок непуст
            float d;
            if (q <= box_hi) d = box_hi - q;
            else if (q >= gm_hi) d = q - gm_hi;
            else d = 0.0f;
            best = std::min(best, d);
        }
        return best;
    }

    // Точная нижняя граница (в квадрате) расстояния до любой непросмотренной
    // занятой точки после оболочки R. Точка вне бокса хотя бы по одной оси
    // (вклад unscanned_dist) и внутри bbox сетки по остальным (вклад interval_dist).
    float shell_lower_bound_sq(float px, float py, float pz, int ecx, int ecy, int ecz, int R) const {
        float gmlo_x = ox + static_cast<float>(gminx) * cell_size;
        float gmhi_x = ox + static_cast<float>(gmaxx + 1) * cell_size;
        float gmlo_y = oy + static_cast<float>(gminy) * cell_size;
        float gmhi_y = oy + static_cast<float>(gmaxy + 1) * cell_size;
        float gmlo_z = oz + static_cast<float>(gminz) * cell_size;
        float gmhi_z = oz + static_cast<float>(gmaxz + 1) * cell_size;

        float blo_x = ox + static_cast<float>(ecx - R)     * cell_size;
        float bhi_x = ox + static_cast<float>(ecx + R + 1) * cell_size;
        float blo_y = oy + static_cast<float>(ecy - R)     * cell_size;
        float bhi_y = oy + static_cast<float>(ecy + R + 1) * cell_size;
        float blo_z = oz + static_cast<float>(ecz - R)     * cell_size;
        float bhi_z = oz + static_cast<float>(ecz + R + 1) * cell_size;

        float gdx = interval_dist(px, gmlo_x, gmhi_x);
        float gdy = interval_dist(py, gmlo_y, gmhi_y);
        float gdz = interval_dist(pz, gmlo_z, gmhi_z);
        float udx = unscanned_dist(px, gmlo_x, gmhi_x, blo_x, bhi_x);
        float udy = unscanned_dist(py, gmlo_y, gmhi_y, blo_y, bhi_y);
        float udz = unscanned_dist(pz, gmlo_z, gmhi_z, blo_z, bhi_z);

        float fx = udx * udx + gdy * gdy + gdz * gdz;   // outside on X
        float fy = gdx * gdx + udy * udy + gdz * gdz;   // outside on Y
        float fz = gdx * gdx + gdy * gdy + udz * udz;   // outside on Z
        return std::min(fx, std::min(fy, fz));
    }

    // Квадрат дистанции до ближайшей точки сетки, но не больше best_sq.
    // best_sq можно передавать как текущий минимум (прунинг по облаку).
    // Оболочки центрируются на ближайшей к запросу занятой ячейке (clamp),
    // поэтому размер поиска всегда ~размер сетки, независимо от удалённости
    // точки запроса. Ранняя остановка — по границам уже просканированного бокса.
    float query_nearest_sq(float px, float py, float pz, float best_sq) const {
        int qx = static_cast<int>(std::floor((px - ox) * inv_cell));
        int qy = static_cast<int>(std::floor((py - oy) * inv_cell));
        int qz = static_cast<int>(std::floor((pz - oz) * inv_cell));
        int ecx = clampi(qx, gminx, gmaxx);   // входная ячейка
        int ecy = clampi(qy, gminy, gmaxy);
        int ecz = clampi(qz, gminz, gmaxz);

        for (int R = 0; R <= max_r; ++R) {
            for (int dx = -R; dx <= R; ++dx) {
                bool edge_x = (dx == -R || dx == R);
                for (int dy = -R; dy <= R; ++dy) {
                    bool edge_xy = edge_x || (dy == -R || dy == R);
                    for (int dz = -R; dz <= R; ++dz) {
                        // Только оболочка Чебышёва радиуса R.
                        if (!edge_xy && dz != -R && dz != R) continue;
                        auto it = cells.find(std::make_tuple(ecx + dx, ecy + dy, ecz + dz));
                        if (it == cells.end()) continue;
                        for (int idx : it->second) {
                            float ddx = px - pts[idx * 3];
                            float ddy = py - pts[idx * 3 + 1];
                            float ddz = pz - pts[idx * 3 + 2];
                            float d = ddx * ddx + ddy * ddy + ddz * ddz;
                            if (d < best_sq) best_sq = d;
                        }
                    }
                }
            }
            // К шагу R просканирован мировой бокс [ec-R, ec+R+1] вокруг входной
            // ячейки. Любая ещё не просмотренная занятая точка лежит вне этого
            // бокса хотя бы по одной оси И внутри bbox сетки по остальным осям.
            // Отсюда точная нижняя граница расстояния до неё; если она уже дальше
            // найденного — дальше искать смысла нет.
            if (best_sq < std::numeric_limits<float>::max()) {
                float floor_sq = shell_lower_bound_sq(px, py, pz, ecx, ecy, ecz, R);
                if (floor_sq >= best_sq) break;
            }
        }
        return best_sq;
    }
};

// Пересекаются ли AABB двух облаков (с небольшим допуском).
// Если облака разнесены, сетка вырождается в перебор всей сетки на каждый
// внешний запрос — тогда честный brute-force не медленнее и без оверхеда.
static bool clouds_bbox_overlap(const std::vector<float>& a, const std::vector<float>& b) {
    float amin[3] = {a[0], a[1], a[2]}, amax[3] = {a[0], a[1], a[2]};
    for (size_t i = 0; i < a.size(); i += 3)
        for (int k = 0; k < 3; ++k) { amin[k] = std::min(amin[k], a[i + k]); amax[k] = std::max(amax[k], a[i + k]); }
    float bmin[3] = {b[0], b[1], b[2]}, bmax[3] = {b[0], b[1], b[2]};
    for (size_t i = 0; i < b.size(); i += 3)
        for (int k = 0; k < 3; ++k) { bmin[k] = std::min(bmin[k], b[i + k]); bmax[k] = std::max(bmax[k], b[i + k]); }
    for (int k = 0; k < 3; ++k) {
        float tol = 0.05f * std::max(amax[k] - amin[k], bmax[k] - bmin[k]);
        if (amin[k] > bmax[k] + tol || bmin[k] > amax[k] + tol) return false;
    }
    return true;
}

// Средняя дистанция (используется для тендера HP -> LP).
// Для каждой lp-вершины ищем ближайшую hp-вершину; hp индексируется в сетку.
float calculate_avg_distance(const std::vector<float>& lp_verts, const std::vector<float>& hp_verts) {
    size_t lp_count = lp_verts.size() / 3;
    size_t hp_count = hp_verts.size() / 3;

    if (lp_count == 0 || hp_count == 0) return std::numeric_limits<float>::max();

    const float FMAX = std::numeric_limits<float>::max();
    bool use_grid = (hp_count > BRUTE_FORCE_REF_MAX) && clouds_bbox_overlap(lp_verts, hp_verts);
    // Сетка строится один раз и только читается из потоков.
    SpatialGrid* grid = use_grid ? new SpatialGrid(hp_verts) : nullptr;

    auto accumulate = [&](size_t start, size_t end) -> double {
        double sum = 0.0;
        for (size_t i = start; i < end; ++i) {
            float px = lp_verts[i * 3];
            float py = lp_verts[i * 3 + 1];
            float pz = lp_verts[i * 3 + 2];
            float best = use_grid ? grid->query_nearest_sq(px, py, pz, FMAX)
                                  : brute_nearest_sq(hp_verts, px, py, pz, FMAX);
            sum += std::sqrt(best);
        }
        return sum;
    };

    double total_sum = 0.0;
    int num_threads = static_cast<int>(std::thread::hardware_concurrency());
    if (num_threads <= 0) num_threads = 4;

    if (lp_count < MIN_PARALLEL_QUERIES || num_threads == 1) {
        total_sum = accumulate(0, lp_count);
    } else {
        std::vector<std::thread> threads;
        std::vector<double> thread_sums(num_threads, 0.0);
        threads.reserve(num_threads);
        for (int t = 0; t < num_threads; ++t) {
            size_t s = (lp_count * t) / num_threads;
            size_t e = (lp_count * (t + 1)) / num_threads;
            threads.emplace_back([&, s, e, t]() { thread_sums[t] = accumulate(s, e); });
        }
        for (auto& th : threads) th.join();
        for (double s : thread_sums) total_sum += s;
    }

    delete grid;
    return static_cast<float>(total_sum / lp_count);
}

float calculate_bidirectional_avg_distance(
    const std::vector<float>& verts_a,
    const std::vector<float>& verts_b)
{
    if (verts_a.empty() || verts_b.empty())
        return std::numeric_limits<float>::max();

    float forward = calculate_avg_distance(verts_a, verts_b);
    float backward = calculate_avg_distance(verts_b, verts_a);

    return (forward + backward) * 0.5f;
}

// НОВОЕ: Минимальная дистанция (используется для флоатеров и декалей).
// Индексируем большее облако в сетку, обходим меньшее. Так как нужен только
// глобальный минимум, текущий минимум пробрасывается в запрос как прунинг —
// оболочки почти всегда обрываются на R=0..1.
float calculate_min_distance(const std::vector<float>& verts_a, const std::vector<float>& verts_b) {
    size_t count_a = verts_a.size() / 3;
    size_t count_b = verts_b.size() / 3;

    if (count_a == 0 || count_b == 0) return std::numeric_limits<float>::max();

    // Сетку строим над большим набором, обходим меньший (меньше запросов).
    const std::vector<float>& big   = (count_a >= count_b) ? verts_a : verts_b;
    const std::vector<float>& small = (count_a >= count_b) ? verts_b : verts_a;
    size_t small_count = small.size() / 3;
    size_t big_count = big.size() / 3;

    // Мелкий референс или разнесённые облака -> честный перебор (без оверхеда сетки).
    if (big_count <= BRUTE_FORCE_REF_MAX || !clouds_bbox_overlap(small, big)) {
        float m = std::numeric_limits<float>::max();
        for (size_t i = 0; i < small_count; ++i) {
            m = brute_nearest_sq(big, small[i * 3], small[i * 3 + 1], small[i * 3 + 2], m);
        }
        return std::sqrt(m);
    }

    SpatialGrid grid(big);

    auto scan = [&](size_t start, size_t end) -> float {
        float local = std::numeric_limits<float>::max();
        for (size_t i = start; i < end; ++i) {
            // Передаём local: запрос вернёт минимум по этой точке, но не больше local.
            local = grid.query_nearest_sq(small[i * 3], small[i * 3 + 1], small[i * 3 + 2], local);
        }
        return local;
    };

    float global_min_sq;
    int num_threads = static_cast<int>(std::thread::hardware_concurrency());
    if (num_threads <= 0) num_threads = 4;

    if (small_count < MIN_PARALLEL_QUERIES || num_threads == 1) {
        global_min_sq = scan(0, small_count);
    } else {
        std::vector<std::thread> threads;
        std::vector<float> thread_mins(num_threads, std::numeric_limits<float>::max());
        threads.reserve(num_threads);
        for (int t = 0; t < num_threads; ++t) {
            size_t s = (small_count * t) / num_threads;
            size_t e = (small_count * (t + 1)) / num_threads;
            threads.emplace_back([&, s, e, t]() { thread_mins[t] = scan(s, e); });
        }
        for (auto& th : threads) th.join();
        global_min_sq = std::numeric_limits<float>::max();
        for (float m : thread_mins) if (m < global_min_sq) global_min_sq = m;
    }

    return std::sqrt(global_min_sq);
}


// Обертки GIL
float py_calculate_avg_distance(const std::vector<float>& lp_verts, const std::vector<float>& hp_verts) {
    py::gil_scoped_release release; 
    return calculate_avg_distance(lp_verts, hp_verts);
}

float py_calculate_bidirectional_avg_distance(
    const std::vector<float>& verts_a,
    const std::vector<float>& verts_b)
{
    py::gil_scoped_release release;
    return calculate_bidirectional_avg_distance(verts_a, verts_b);
}

float py_calculate_min_distance(const std::vector<float>& verts_a, const std::vector<float>& verts_b) {
    py::gil_scoped_release release;
    return calculate_min_distance(verts_a, verts_b);
}

// ============================================================================
// 2. АНАЛИЗ HP И КОЛЛИЗИЙ
// ============================================================================

bool check_mesh_collision(const std::vector<float>& verts_a, const std::vector<float>& verts_b, float threshold) {
    if (verts_a.empty() || verts_b.empty()) return false;

    float cell_size = threshold * 2.0f;
    std::unordered_map<std::tuple<int, int, int>, bool, PosHash> grid;

    for (size_t i = 0; i < verts_a.size(); i += 3) {
        int gx = static_cast<int>(std::floor(verts_a[i] / cell_size));
        int gy = static_cast<int>(std::floor(verts_a[i+1] / cell_size));
        int gz = static_cast<int>(std::floor(verts_a[i+2] / cell_size));
        grid[{gx, gy, gz}] = true;
    }

    for (size_t i = 0; i < verts_b.size(); i += 3) {
        int gx = static_cast<int>(std::floor(verts_b[i] / cell_size));
        int gy = static_cast<int>(std::floor(verts_b[i+1] / cell_size));
        int gz = static_cast<int>(std::floor(verts_b[i+2] / cell_size));

        for (int dx = -1; dx <= 1; ++dx) {
            for (int dy = -1; dy <= 1; ++dy) {
                for (int dz = -1; dz <= 1; ++dz) {
                    if (grid.count({gx + dx, gy + dy, gz + dz})) {
                        return true; 
                    }
                }
            }
        }
    }
    return false;
}

bool py_check_mesh_collision(const std::vector<float>& verts_a, const std::vector<float>& verts_b, float threshold) {
    py::gil_scoped_release release;
    return check_mesh_collision(verts_a, verts_b, threshold);
}

bool are_symmetric_axis(
    const std::vector<float>& verts_a,
    const std::vector<float>& verts_b,
    int axis,
    float tolerance,
    float min_match_ratio)
{
    size_t count_a = verts_a.size() / 3;
    size_t count_b = verts_b.size() / 3;
    if (count_a == 0 || count_a != count_b) return false;

    float center_a = 0.0f;
    float center_b = 0.0f;
    for (size_t i = 0; i < count_a; ++i) {
        center_a += verts_a[i * 3 + axis];
        center_b += verts_b[i * 3 + axis];
    }
    center_a /= static_cast<float>(count_a);
    center_b /= static_cast<float>(count_b);

    float mirror_plane = (center_a + center_b) * 0.5f;
    float cell_size = std::max(tolerance, 1e-6f);
    float tolerance_sq = tolerance * tolerance;

    std::unordered_map<std::tuple<int, int, int>, std::vector<size_t>, PosHash> grid;
    grid.reserve(count_b * 2);

    for (size_t i = 0; i < count_b; ++i) {
        float x = verts_b[i * 3];
        float y = verts_b[i * 3 + 1];
        float z = verts_b[i * 3 + 2];
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) continue;
        int gx = static_cast<int>(std::floor(x / cell_size));
        int gy = static_cast<int>(std::floor(y / cell_size));
        int gz = static_cast<int>(std::floor(z / cell_size));
        grid[{gx, gy, gz}].push_back(i);
    }

    std::vector<unsigned char> used(count_b, 0);
    size_t matched = 0;

    for (size_t i = 0; i < count_a; ++i) {
        float p[3] = {verts_a[i * 3], verts_a[i * 3 + 1], verts_a[i * 3 + 2]};
        if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2])) continue;
        p[axis] = mirror_plane * 2.0f - p[axis];

        int gx = static_cast<int>(std::floor(p[0] / cell_size));
        int gy = static_cast<int>(std::floor(p[1] / cell_size));
        int gz = static_cast<int>(std::floor(p[2] / cell_size));

        bool found = false;
        for (int dx = -1; dx <= 1 && !found; ++dx) {
            for (int dy = -1; dy <= 1 && !found; ++dy) {
                for (int dz = -1; dz <= 1 && !found; ++dz) {
                    auto it = grid.find({gx + dx, gy + dy, gz + dz});
                    if (it == grid.end()) continue;
                    for (size_t idx : it->second) {
                        if (used[idx]) continue;
                        float bx = verts_b[idx * 3];
                        float by = verts_b[idx * 3 + 1];
                        float bz = verts_b[idx * 3 + 2];
                        float ddx = p[0] - bx;
                        float ddy = p[1] - by;
                        float ddz = p[2] - bz;
                        if ((ddx * ddx + ddy * ddy + ddz * ddz) <= tolerance_sq) {
                            used[idx] = 1;
                            ++matched;
                            found = true;
                            break;
                        }
                    }
                }
            }
        }
    }

    return (static_cast<float>(matched) / static_cast<float>(count_a)) >= min_match_ratio;
}

bool are_symmetric(const std::vector<float>& verts_a, const std::vector<float>& verts_b, float tolerance) {
    if (verts_a.size() < 9 || verts_b.size() < 9) return false;
    if ((verts_a.size() % 3) != 0 || (verts_b.size() % 3) != 0) return false;
    if ((verts_a.size() / 3) != (verts_b.size() / 3)) return false;

    float safe_tolerance = std::max(tolerance, 1e-6f);
    const float min_match_ratio = 0.85f;
    for (int axis = 0; axis < 3; ++axis) {
        if (are_symmetric_axis(verts_a, verts_b, axis, safe_tolerance, min_match_ratio)) {
            return true;
        }
    }
    return false;
}

bool py_are_symmetric(const std::vector<float>& verts_a, const std::vector<float>& verts_b, float tolerance) {
    py::gil_scoped_release release;
    return are_symmetric(verts_a, verts_b, tolerance);
}

std::string generate_fingerprint_data(const std::vector<float>& verts, const std::vector<float>& center) {
    if (verts.size() < 3 || center.size() < 3) return "empty";
    
    float cx = center[0];
    float cy = center[1];
    float cz = center[2];
    
    size_t num_verts = verts.size() / 3;
    std::vector<float> distances;
    distances.reserve(num_verts);
    
    for (size_t i = 0; i < num_verts; ++i) {
        float dx = verts[i * 3] - cx;
        float dy = verts[i * 3 + 1] - cy;
        float dz = verts[i * 3 + 2] - cz;
        distances.push_back(std::sqrt(dx * dx + dy * dy + dz * dz));
    }
    
    std::sort(distances.begin(), distances.end());
    
    std::ostringstream oss;
    oss << "v" << num_verts;
    
    if (num_verts > 0) {
        const int num_samples = 20;
        for (int i = 0; i <= num_samples; ++i) {
            size_t idx = (i * (num_verts - 1)) / num_samples;
            oss << "_" << std::fixed << std::setprecision(4) << distances[idx];
        }
    }
    
    return oss.str();
}

int resolve_hp_collision(const std::vector<float>& hp_verts, const std::vector<std::vector<float>>& lp_candidates_verts) {
    size_t hp_count = hp_verts.size() / 3;
    if (hp_count == 0 || lp_candidates_verts.empty()) return 0;
    
    size_t sample_step = 1;
    if (hp_count > 120) {
        sample_step = hp_count / 120;
    }
    
    int best_idx = 0;
    double min_total_distance = std::numeric_limits<double>::max();
    size_t num_candidates = lp_candidates_verts.size();
    
    for (size_t c = 0; c < num_candidates; ++c) {
        const auto& lp_verts = lp_candidates_verts[c];
        size_t lp_count = lp_verts.size() / 3;
        if (lp_count == 0) continue;
        
        double current_candidate_distance = 0.0;
        size_t samples_checked = 0;
        
        size_t lp_step = 1;
        if (lp_count > 250) {
            lp_step = lp_count / 250;
        }
        
        for (size_t i = 0; i < hp_count; i += sample_step) {
            float h_x = hp_verts[i * 3];
            float h_y = hp_verts[i * 3 + 1];
            float h_z = hp_verts[i * 3 + 2];
            
            float min_v_dist = std::numeric_limits<float>::max();
            
            for (size_t j = 0; j < lp_count; j += lp_step) {
                float dx = h_x - lp_verts[j * 3];
                float dy = h_y - lp_verts[j * 3 + 1];
                float dz = h_z - lp_verts[j * 3 + 2];
                float d2 = dx * dx + dy * dy + dz * dz;
                if (d2 < min_v_dist) {
                    min_v_dist = d2;
                }
            }
            current_candidate_distance += std::sqrt(min_v_dist);
            samples_checked++;
        }
        
        if (samples_checked > 0) {
            current_candidate_distance /= samples_checked;
        }
        
        if (current_candidate_distance < min_total_distance) {
            min_total_distance = current_candidate_distance;
            best_idx = static_cast<int>(c);
        }
    }
    
    return best_idx;
}

int py_resolve_hp_collision(const std::vector<float>& hp_verts, const std::vector<std::vector<float>>& lp_candidates_verts) {
    py::gil_scoped_release release;
    return resolve_hp_collision(hp_verts, lp_candidates_verts);
}


// ============================================================================
// 3. ФУНКЦИИ АНАЛИЗА ФОРМЫ (PCA + Центроиды)
// ============================================================================

std::vector<std::tuple<int, int, float, float, int, float>> calculate_vertex_owner_scores(
    const std::vector<std::vector<float>>& lp_point_sets,
    const std::vector<std::vector<float>>& hp_point_sets,
    const std::vector<std::pair<int, int>>& candidate_pairs)
{
    size_t lp_count = lp_point_sets.size();
    size_t hp_count = hp_point_sets.size();
    std::vector<std::vector<int>> hp_candidates_by_lp(lp_count);
    std::vector<std::vector<int>> lp_candidates_by_hp(hp_count);

    for (const auto& pair : candidate_pairs) {
        int lp_idx = pair.first;
        int hp_idx = pair.second;
        if (lp_idx < 0 || hp_idx < 0) continue;
        if (static_cast<size_t>(lp_idx) >= lp_count || static_cast<size_t>(hp_idx) >= hp_count) continue;
        hp_candidates_by_lp[lp_idx].push_back(hp_idx);
        lp_candidates_by_hp[hp_idx].push_back(lp_idx);
    }

    std::vector<std::vector<float>> lp_claim(lp_count, std::vector<float>(hp_count, 0.0f));
    std::vector<std::vector<float>> hp_owner(lp_count, std::vector<float>(hp_count, 0.0f));

    int num_threads = std::thread::hardware_concurrency();
    if (num_threads <= 0) num_threads = 4;

    auto lp_worker = [&](int thread_id) {
        size_t start = (lp_count * thread_id) / num_threads;
        size_t end = (lp_count * (thread_id + 1)) / num_threads;
        for (size_t lp_idx = start; lp_idx < end; ++lp_idx) {
            const auto& lp_points = lp_point_sets[lp_idx];
            const auto& candidates = hp_candidates_by_lp[lp_idx];
            size_t point_count = lp_points.size() / 3;
            if (point_count == 0 || candidates.empty()) continue;

            std::vector<int> counts(hp_count, 0);
            for (size_t p = 0; p < point_count; ++p) {
                float px = lp_points[p * 3];
                float py = lp_points[p * 3 + 1];
                float pz = lp_points[p * 3 + 2];
                int best_hp = -1;
                float best_dist = std::numeric_limits<float>::max();
                for (int hp_idx : candidates) {
                    const auto& hp_points = hp_point_sets[hp_idx];
                    size_t hp_points_count = hp_points.size() / 3;
                    for (size_t q = 0; q < hp_points_count; ++q) {
                        float dx = px - hp_points[q * 3];
                        float dy = py - hp_points[q * 3 + 1];
                        float dz = pz - hp_points[q * 3 + 2];
                        float dist = dx * dx + dy * dy + dz * dz;
                        if (dist < best_dist) {
                            best_dist = dist;
                            best_hp = hp_idx;
                        }
                    }
                }
                if (best_hp >= 0) counts[best_hp]++;
            }
            for (int hp_idx : candidates) {
                lp_claim[lp_idx][hp_idx] = (static_cast<float>(counts[hp_idx]) / static_cast<float>(point_count)) * 100.0f;
            }
        }
    };

    std::vector<std::thread> threads;
    threads.reserve(num_threads);
    for (int i = 0; i < num_threads; ++i) {
        threads.emplace_back(lp_worker, i);
    }
    for (auto& t : threads) t.join();

    threads.clear();
    auto hp_worker = [&](int thread_id) {
        size_t start = (hp_count * thread_id) / num_threads;
        size_t end = (hp_count * (thread_id + 1)) / num_threads;
        for (size_t hp_idx = start; hp_idx < end; ++hp_idx) {
            const auto& hp_points = hp_point_sets[hp_idx];
            const auto& candidates = lp_candidates_by_hp[hp_idx];
            size_t point_count = hp_points.size() / 3;
            if (point_count == 0 || candidates.empty()) continue;

            std::vector<int> counts(lp_count, 0);
            for (size_t p = 0; p < point_count; ++p) {
                float px = hp_points[p * 3];
                float py = hp_points[p * 3 + 1];
                float pz = hp_points[p * 3 + 2];
                int best_lp = -1;
                float best_dist = std::numeric_limits<float>::max();
                for (int lp_idx : candidates) {
                    const auto& lp_points = lp_point_sets[lp_idx];
                    size_t lp_points_count = lp_points.size() / 3;
                    for (size_t q = 0; q < lp_points_count; ++q) {
                        float dx = px - lp_points[q * 3];
                        float dy = py - lp_points[q * 3 + 1];
                        float dz = pz - lp_points[q * 3 + 2];
                        float dist = dx * dx + dy * dy + dz * dz;
                        if (dist < best_dist) {
                            best_dist = dist;
                            best_lp = lp_idx;
                        }
                    }
                }
                if (best_lp >= 0) counts[best_lp]++;
            }
            for (int lp_idx : candidates) {
                hp_owner[lp_idx][hp_idx] = (static_cast<float>(counts[lp_idx]) / static_cast<float>(point_count)) * 100.0f;
            }
        }
    };

    for (int i = 0; i < num_threads; ++i) {
        threads.emplace_back(hp_worker, i);
    }
    for (auto& t : threads) t.join();

    std::vector<int> owner_lp_by_hp(hp_count, -1);
    std::vector<float> owner_pct_by_hp(hp_count, 0.0f);
    for (size_t hp_idx = 0; hp_idx < hp_count; ++hp_idx) {
        for (size_t lp_idx = 0; lp_idx < lp_count; ++lp_idx) {
            float value = hp_owner[lp_idx][hp_idx];
            if (owner_lp_by_hp[hp_idx] < 0 || value > owner_pct_by_hp[hp_idx]) {
                owner_lp_by_hp[hp_idx] = static_cast<int>(lp_idx);
                owner_pct_by_hp[hp_idx] = value;
            }
        }
    }

    std::vector<std::tuple<int, int, float, float, int, float>> result;
    result.reserve(candidate_pairs.size());
    for (const auto& pair : candidate_pairs) {
        int lp_idx = pair.first;
        int hp_idx = pair.second;
        if (lp_idx < 0 || hp_idx < 0) continue;
        if (static_cast<size_t>(lp_idx) >= lp_count || static_cast<size_t>(hp_idx) >= hp_count) continue;
        result.emplace_back(lp_idx, hp_idx, lp_claim[lp_idx][hp_idx], hp_owner[lp_idx][hp_idx], owner_lp_by_hp[hp_idx], owner_pct_by_hp[hp_idx]);
    }
    return result;
}

std::vector<std::tuple<int, int, float, float, int, float>> py_calculate_vertex_owner_scores(
    const std::vector<std::vector<float>>& lp_point_sets,
    const std::vector<std::vector<float>>& hp_point_sets,
    const std::vector<std::pair<int, int>>& candidate_pairs)
{
    py::gil_scoped_release release;
    return calculate_vertex_owner_scores(lp_point_sets, hp_point_sets, candidate_pairs);
}

MeshMetrics analyze_mesh_shape(const std::vector<float>& verts) {
    MeshMetrics m;
    size_t n = verts.size() / 3;
    if (n < 3) return m;

    float cx = 0, cy = 0, cz = 0;
    float min_x = verts[0], max_x = verts[0];
    float min_y = verts[1], max_y = verts[1];
    float min_z = verts[2], max_z = verts[2];

    for (size_t i = 0; i < verts.size(); i += 3) {
        cx += verts[i]; cy += verts[i+1]; cz += verts[i+2];
        min_x = std::min(min_x, verts[i]); max_x = std::max(max_x, verts[i]);
        min_y = std::min(min_y, verts[i+1]); max_y = std::max(max_y, verts[i+1]);
        min_z = std::min(min_z, verts[i+2]); max_z = std::max(max_z, verts[i+2]);
    }
    cx /= n; cy /= n; cz /= n;
    
    float geom_center_x = (min_x + max_x) * 0.5f;
    float geom_center_y = (min_y + max_y) * 0.5f;
    float geom_center_z = (min_z + max_z) * 0.5f;

    m.symmetry_score = std::sqrt(std::pow(cx - geom_center_x, 2) + 
                                 std::pow(cy - geom_center_y, 2) + 
                                 std::pow(cz - geom_center_z, 2));

    m.center = {geom_center_x, geom_center_y, geom_center_z};

    float cov_xx = 0, cov_yy = 0, cov_zz = 0;
    for (size_t i = 0; i < verts.size(); i += 3) {
        cov_xx += std::pow(verts[i] - cx, 2);
        cov_yy += std::pow(verts[i+1] - cy, 2);
        cov_zz += std::pow(verts[i+2] - cz, 2);
    }
    
    std::vector<float> axes = {cov_xx, cov_yy, cov_zz};
    std::sort(axes.begin(), axes.end());
    
    m.elongation = (axes[0] > 0) ? std::sqrt(axes[2] / axes[0]) : 1.0f;
    m.dimensions = {std::sqrt(axes[2]), std::sqrt(axes[1]), std::sqrt(axes[0])};

    return m;
}


// ============================================================================
// 4. РЕГИСТРАЦИЯ МОДУЛЯ ДЛЯ PYTHON
// ============================================================================
PYBIND11_MODULE(bg_math_core, m) {
    m.doc() = "Optimized High-performance C++ math utilities for Bake Groups tool";
    
    m.def(
        "calculate_bidirectional_avg_distance",
        &py_calculate_bidirectional_avg_distance,
        "Symmetric average nearest-neighbor distance"
    );

    py::class_<MeshMetrics>(m, "MeshMetrics")
        .def_readonly("elongation", &MeshMetrics::elongation)
        .def_readonly("symmetry_score", &MeshMetrics::symmetry_score)
        .def_readonly("dimensions", &MeshMetrics::dimensions)
        .def_readonly("center", &MeshMetrics::center);

    m.def("calculate_avg_distance", &py_calculate_avg_distance, "Calculate average distance between two vertex clouds (Multi-threaded)");
    
    // Новая функция вынесена в Python-пространство
    m.def("calculate_min_distance", &py_calculate_min_distance, "Calculate absolute minimum distance between two vertex clouds (Multi-threaded)");
    
    m.def("check_mesh_collision", &py_check_mesh_collision, "Fast spatial hash-based collision detection between vertex clouds");
    m.def("are_symmetric", &py_are_symmetric, py::arg("verts_a"), py::arg("verts_b"), py::arg("tolerance") = 0.01f, "Check mirrored point-cloud symmetry across the best world axis");
    m.def("resolve_hp_collision", &py_resolve_hp_collision, "Resolve high-poly to low-poly candidate assignment collisions");
    m.def("calculate_vertex_owner_scores", &py_calculate_vertex_owner_scores, "Calculate LP/HP nearest-vertex ownership scores for candidate pairs");
    
    m.def("analyze_mesh_shape", &analyze_mesh_shape, "Analyze mesh elongation and symmetry using PCA principles");
    m.def("generate_fingerprint_data", &generate_fingerprint_data, "Generate a geometric string fingerprint for a mesh");
}
