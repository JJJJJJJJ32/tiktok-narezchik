// silence_finder — поиск пауз в PCM-аудио (16 кГц, mono, s16le) на stdin.
// Для каждой целевой точки реза (45, 90, 135… с) возвращает ближайшую паузу
// в окне ±W секунд. Результат — JSON в stdout.
//
// Сборка: см. CMakeLists.txt или build_cpp.py. Зависимостей нет, только STL.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {

struct Options {
    double clip_duration = 45.0;
    double window = 3.0;
    double frame_ms = 20.0;
    double percentile = 15.0;
    double factor = 2.5;
    double median_factor = 0.25;
    double min_threshold = 0.002;
    double min_pause = 0.15;
    int sample_rate = 16000;
};

struct Pause {
    double start;
    double end;
    double center() const { return (start + end) / 2.0; }
};

std::vector<char> read_stdin() {
    std::vector<char> data;
    char buf[1 << 16];
    while (std::cin) {
        std::cin.read(buf, sizeof(buf));
        std::streamsize n = std::cin.gcount();
        if (n > 0) data.insert(data.end(), buf, buf + n);
    }
    return data;
}

// Линейная интерполяция — та же формула, что numpy.percentile (linear).
double percentile_linear(std::vector<double> values, double p) {
    if (values.empty()) return 0.0;
    std::sort(values.begin(), values.end());
    double pos = (static_cast<double>(values.size()) - 1.0) * p / 100.0;
    size_t lo = static_cast<size_t>(std::floor(pos));
    size_t hi = std::min(lo + 1, values.size() - 1);
    double frac = pos - static_cast<double>(lo);
    return values[lo] * (1.0 - frac) + values[hi] * frac;
}

std::string json_number(double v) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.3f", v);
    return std::string(buf);
}

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
    // Без этого Windows-консоль обрывает PCM на байте 0x1A (Ctrl+Z).
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif

    Options opt;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        auto next = [&](double def) -> double {
            return (i + 1 < argc) ? std::atof(argv[++i]) : def;
        };
        if (arg == "--clip-duration") opt.clip_duration = next(opt.clip_duration);
        else if (arg == "--window") opt.window = next(opt.window);
        else if (arg == "--frame-ms") opt.frame_ms = next(opt.frame_ms);
        else if (arg == "--percentile") opt.percentile = next(opt.percentile);
        else if (arg == "--factor") opt.factor = next(opt.factor);
        else if (arg == "--median-factor") opt.median_factor = next(opt.median_factor);
        else if (arg == "--min-threshold") opt.min_threshold = next(opt.min_threshold);
        else if (arg == "--min-pause") opt.min_pause = next(opt.min_pause);
        else if (arg == "--sample-rate") opt.sample_rate = static_cast<int>(next(opt.sample_rate));
        else if (arg == "--help" || arg == "-h") {
            std::cout << "usage: silence_finder [--clip-duration 45] [--window 3]\n";
            return 0;
        }
    }

    std::vector<char> raw = read_stdin();
    const size_t sample_count = raw.size() / sizeof(int16_t);
    const int16_t* samples = reinterpret_cast<const int16_t*>(raw.data());

    const int frame = std::max(1, static_cast<int>(std::lround(opt.frame_ms * opt.sample_rate / 1000.0)));
    const double duration = static_cast<double>(sample_count) / opt.sample_rate;

    std::vector<double> rms;
    rms.reserve(sample_count / frame + 1);
    for (size_t i = 0; i + frame <= sample_count; i += frame) {
        double acc = 0.0;
        for (int j = 0; j < frame; ++j) {
            double s = samples[i + j] / 32768.0;
            acc += s * s;
        }
        rms.push_back(std::sqrt(acc / frame));
    }

    const double noise = percentile_linear(rms, opt.percentile);
    // Порог = шум * коэффициент, но не выше доли медианной энергии:
    // иначе запись без пауз целиком считается «тишиной».
    const double median = percentile_linear(rms, 50.0);
    const double threshold = std::max(
        opt.min_threshold,
        std::min(noise * opt.factor, median * opt.median_factor));

    // Собираем паузы: подряд идущие тихие окна длиной не меньше min_pause.
    std::vector<Pause> pauses;
    const int min_frames = static_cast<int>(std::ceil(opt.min_pause * 1000.0 / opt.frame_ms));
    int run_begin = -1;
    for (size_t i = 0; i <= rms.size(); ++i) {
        const bool quiet = (i < rms.size()) && (rms[i] < threshold);
        if (quiet && run_begin < 0) {
            run_begin = static_cast<int>(i);
        } else if (!quiet && run_begin >= 0) {
            if (static_cast<int>(i) - run_begin >= min_frames) {
                pauses.push_back({run_begin * frame / static_cast<double>(opt.sample_rate),
                                  static_cast<int>(i) * frame / static_cast<double>(opt.sample_rate)});
            }
            run_begin = -1;
        }
    }

    // Точки реза: каждая следующая цель = предыдущий рез + clip_duration,
    // чтобы клипы не расползались из-за сдвигов.
    std::vector<double> cuts;
    double previous = 0.0;
    while (previous + opt.clip_duration <= duration + 1e-9) {
        const double target = previous + opt.clip_duration;
        double best = target;
        double best_dist = opt.window + 1e-9;
        for (const auto& p : pauses) {
            const double d = std::fabs(p.center() - target);
            if (d <= best_dist) {
                best_dist = d;
                best = p.center();
            }
        }
        cuts.push_back(best);
        previous = best;
    }

    std::cout << "{\"duration\":" << json_number(duration) << ",\"threshold\":"
              << json_number(threshold) << ",\"cuts\":[";
    for (size_t i = 0; i < cuts.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << json_number(cuts[i]);
    }
    std::cout << "],\"silences\":[";
    for (size_t i = 0; i < pauses.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << '[' << json_number(pauses[i].start) << ',' << json_number(pauses[i].end) << ']';
    }
    std::cout << "]}\n";
    return 0;
}
