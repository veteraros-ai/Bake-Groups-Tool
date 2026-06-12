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
// 1. АНАЛИЗ ДИСТАНЦИЙ (С МНОГОПОТОЧНОСТЬЮ)
// ============================================================================

// Средняя дистанция (используется для тендера HP -> LP)
float calculate_avg_distance(const std::vector<float>& lp_verts, const std::vector<float>& hp_verts) {
    int lp_count = lp_verts.size() / 3;
    int hp_count = hp_verts.size() / 3;

    if (lp_count == 0 || hp_count == 0) return std::numeric_limits<float>::max();

    int num_threads = std::thread::hardware_concurrency();
    if (num_threads == 0) num_threads = 4;

    std::vector<std::thread> threads;
    std::vector<double> thread_sums(num_threads, 0.0);

    auto worker = [&](int thread_id) {
        int start = (lp_count * thread_id) / num_threads;
        int end = (lp_count * (thread_id + 1)) / num_threads;

        for (int i = start; i < end; ++i) {
            float px = lp_verts[i * 3];
            float py = lp_verts[i * 3 + 1];
            float pz = lp_verts[i * 3 + 2];

            float min_dist_sq = std::numeric_limits<float>::max();

            for (int j = 0; j < hp_count; ++j) {
                float dx = px - hp_verts[j * 3];
                float dy = py - hp_verts[j * 3 + 1];
                float dz = pz - hp_verts[j * 3 + 2];
                float dist_sq = dx * dx + dy * dy + dz * dz;
                if (dist_sq < min_dist_sq) {
                    min_dist_sq = dist_sq;
                }
            }
            thread_sums[thread_id] += std::sqrt(min_dist_sq);
        }
    };

    for (int i = 0; i < num_threads; ++i) {
        threads.emplace_back(worker, i);
    }

    for (auto& t : threads) {
        t.join();
    }

    double total_sum = 0.0;
    for (double s : thread_sums) {
        total_sum += s;
    }

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

// НОВОЕ: Минимальная дистанция (используется для флоатеров и декалей)
float calculate_min_distance(const std::vector<float>& verts_a, const std::vector<float>& verts_b) {
    size_t count_a = verts_a.size() / 3;
    size_t count_b = verts_b.size() / 3;

    if (count_a == 0 || count_b == 0) return std::numeric_limits<float>::max();

    int num_threads = std::thread::hardware_concurrency();
    if (num_threads == 0) num_threads = 4;

    std::vector<std::thread> threads;
    std::vector<float> thread_mins(num_threads, std::numeric_limits<float>::max());

    auto worker = [&](int thread_id) {
        size_t start = (count_a * thread_id) / num_threads;
        size_t end = (count_a * (thread_id + 1)) / num_threads;

        float local_min_sq = std::numeric_limits<float>::max();

        for (size_t i = start; i < end; ++i) {
            float px = verts_a[i * 3];
            float py = verts_a[i * 3 + 1];
            float pz = verts_a[i * 3 + 2];

            for (size_t j = 0; j < count_b; ++j) {
                float dx = px - verts_b[j * 3];
                float dy = py - verts_b[j * 3 + 1];
                float dz = pz - verts_b[j * 3 + 2];
                float dist_sq = dx * dx + dy * dy + dz * dz;
                
                if (dist_sq < local_min_sq) {
                    local_min_sq = dist_sq;
                }
            }
        }
        thread_mins[thread_id] = local_min_sq;
    };

    for (int i = 0; i < num_threads; ++i) {
        threads.emplace_back(worker, i);
    }

    for (auto& t : threads) {
        t.join();
    }

    float global_min_sq = std::numeric_limits<float>::max();
    for (float m : thread_mins) {
        if (m < global_min_sq) global_min_sq = m;
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
