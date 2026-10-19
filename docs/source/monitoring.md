# System Monitoring & Plotting

`orchestrant.monitoring` records CPU, memory and GPU (NVIDIA and AMD) metrics while Python code
runs, summarises them and plots them.

- **Metrics:** CPU and RAM usage; GPU utilization, memory, temperature and power draw (NVIDIA
  everywhere, AMD on Linux or via ADL PMLog).
- **Logging:** configurable sampling intervals, structured metric storage, statistical
  summaries.
- **Plots** (matplotlib): CPU and memory, GPU utilization, GPU memory and GPU temperature, alone
  or as one dashboard; interactive, or exported to PNG/PDF.

## Installation

The dependencies come with the package:

```bash
pip install -e .
```

- `psutil` - CPU and memory monitoring
- `nvidia-ml-py` - NVIDIA GPU monitoring (optional, NVIDIA hosts only)
- `matplotlib` - Visualization

AMD needs no extra package: on Windows the monitor uses the ADL library that ships with the
graphics driver, on Linux the `amdgpu` sysfs counters. The `gpu-rocm` extra installs AMD SMI
(`amdsmi`), which is optional and only resolves the card's marketing name.

## Quick start

```python
from orchestrant import SystemMonitor, MetricsPlotter, quick_plot

monitor = SystemMonitor(interval=0.5)
monitor.start()

for i in range(10):
    # Do some work
    monitor.record()  # Take a snapshot

monitor.stop()
monitor.print_summary()

plotter = MetricsPlotter(monitor.get_metrics())
plotter.plot_all()
plotter.save_figure("output/metrics.png")
plotter.show()  # Display interactively

# or in one line:
quick_plot(monitor.get_metrics(), output_path="metrics.png", show=True)
```

The same start/record/stop pattern profiles a training run, a long-running batch job (a larger
`interval`, `record()` per batch) or two implementations side by side (one monitor each, then
`print_summary()` on both).

`python examples/monitoring.py` runs three examples: basic monitoring, monitoring with
visualization, and continuous background monitoring through different workload phases.

## API

### SystemMonitor

```python
SystemMonitor(interval=1.0, gpu_index=0)
```

- `interval`: Time between measurements (seconds)
- `gpu_index`: GPU device index to monitor

Methods: `start()` starts a session, `record()` records a snapshot, `stop()` stops the session,
`get_metrics()` returns every snapshot, `print_summary()` prints the statistics.

### SystemMetrics

One snapshot:

| Attribute | Meaning |
| --- | --- |
| `timestamp` | Unix timestamp |
| `cpu_percent` | CPU usage (%) |
| `memory_percent` | Memory usage (%) |
| `memory_used_mb` | Used memory (MB) |
| `memory_available_mb` | Available memory (MB) |
| `gpu_utilization` | GPU usage (%), optional |
| `gpu_memory_used_mb` | GPU memory used (MB), optional |
| `gpu_memory_total_mb` | Total GPU memory (MB), optional |
| `gpu_temperature` | GPU temperature (°C), optional |

### MetricsPlotter

```python
MetricsPlotter(metrics: Sequence[SystemMetrics])
```

- `plot_cpu_memory(ax=None)`, `plot_gpu_utilization(ax=None)`, `plot_gpu_memory(ax=None)`,
  `plot_gpu_temperature(ax=None)` - one plot each
- `plot_all(figsize=(14, 10))` - the whole dashboard
- `save_figure(filepath, dpi=150)` - save to a file
- `show()` - display interactively

### quick_plot()

```python
quick_plot(metrics, output_path=None, *, show=True)
```

## GPU support

The monitor probes NVIDIA first (NVML, the `nvidia-ml-py` extra), then AMD, and exposes the same
metrics either way:

- **NVIDIA**: any host where `nvidia-ml-py` imports; requires the driver.
- **AMD on Windows**: the AMD Display Library (`atiadlxx.dll`) installed by
  the graphics driver — no Python package. Utilization, temperature and power
  come from the PMLog sensors; VRAM from the adapter memory APIs. AMD adapters
  are ordered with the largest dedicated VRAM first, so `gpu_index=0` is the
  discrete GPU on an APU+dGPU machine.
- **AMD on Linux**: the `amdgpu` sysfs counters under
  `/sys/class/drm/card*/device` — no package, no privileges.

If no backend answers, monitoring runs without GPU metrics. `GPUProbe.vendor` names the vendor
that answered (`"nvidia"`, `"amd"` or `"none"`) and `GPUProbe.backend` the mechanism (`nvml`,
`adl`, `sysfs`).

## Troubleshooting

### GPU not detected

NVIDIA:

1. The driver is installed:
   ```bash
   nvidia-smi
   ```

2. `nvidia-ml-py` is installed:
   ```bash
   pip list | grep nvidia-ml-py
   ```

3. NVML answers:
   ```python
   import pynvml

   pynvml.nvmlInit()
   print(pynvml.nvmlDeviceGetCount())
   ```

AMD:

1. Linux: the kernel driver is loaded and the counters exist:
   ```bash
   ls /sys/class/drm/card*/device/gpu_busy_percent
   ```
   Monitoring needs no root and no ROCm installation; `amdsmi`
   (`uv sync --extra gpu-rocm`) only improves the reported card name.

2. Windows: the graphics driver is installed (`atiadlxx.dll` is part of it) and another
   monitoring tool can see the card. The monitor probes it lazily; when in doubt, check
   `GPUProbe().vendor` and `GPUProbe().backend`:

   ```python
   from orchestrant.monitoring.gpu import GPUProbe

   probe = GPUProbe()
   print(probe.vendor, probe.backend, probe.gpu_name)
   print(probe.read())
   ```

### High memory usage

The monitor keeps every snapshot in memory. For a long session use a larger sampling interval,
save and clear the metrics periodically, or stream them to a database.
