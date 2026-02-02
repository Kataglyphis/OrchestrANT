# System Monitoring & Plotting

This package provides comprehensive system monitoring and visualization capabilities for tracking CPU, GPU (NVIDIA), and memory metrics during Python application execution.

## Features

- **Real-time System Monitoring**
  - CPU usage tracking
  - Memory (RAM) usage tracking
  - NVIDIA GPU utilization monitoring
  - GPU memory usage tracking
  - GPU temperature monitoring

- **Data Logging**
  - Configurable sampling intervals
  - Structured metric storage
  - Statistical summaries

- **Visualization**
  - Interactive plots with matplotlib
  - Comprehensive dashboards
  - Export to PNG/PDF
  - CPU and memory usage plots
  - GPU utilization and memory plots
  - GPU temperature plots

## Installation

The required dependencies are already included in the package:

```bash
pip install -e .
```

Dependencies:
- `psutil` - CPU and memory monitoring
- `nvidia-ml-py` - NVIDIA GPU monitoring (optional, only if you have NVIDIA GPU)
- `matplotlib` - Visualization

## Quick Start

### Basic Monitoring

```python
from orchestr_ant_ion import SystemMonitor

# Create monitor with 0.5 second sampling interval
monitor = SystemMonitor(interval=0.5)

# Start monitoring
monitor.start()

# Your code here...
for i in range(10):
    # Do some work
    monitor.record()  # Take a snapshot

# Stop and print summary
monitor.stop()
monitor.print_summary()
```

### Monitoring with Visualization

```python
from orchestr_ant_ion import SystemMonitor, MetricsPlotter

# Monitor your application
monitor = SystemMonitor(interval=0.3)
monitor.start()

# Your application code...
for i in range(20):
    # Do work
    monitor.record()

monitor.stop()

# Create visualizations
plotter = MetricsPlotter(monitor.get_metrics())
plotter.plot_all()
plotter.save_figure("output/metrics.png")
plotter.show()  # Display interactively
```

### Quick Plot (Convenience Function)

```python
from orchestr_ant_ion import SystemMonitor, quick_plot

monitor = SystemMonitor()
monitor.start()

# Your code...
for i in range(10):
    # Work
    monitor.record()

monitor.stop()

# One-line plotting
quick_plot(monitor.get_metrics(), output_path="metrics.png", show=True)
```

## Examples

Run the included example script:

```bash
python examples_monitoring.py
```

This will demonstrate:
1. Basic system monitoring
2. Monitoring with visualization
3. Continuous background monitoring with different workload phases

## API Reference

### SystemMonitor

Main class for monitoring system resources.

**Constructor:**
```python
SystemMonitor(interval=1.0, gpu_index=0)
```
- `interval`: Time between measurements (seconds)
- `gpu_index`: GPU device index to monitor

**Methods:**
- `start()` - Start monitoring session
- `record()` - Record a metric snapshot
- `stop()` - Stop monitoring session
- `get_metrics()` - Get all collected metrics
- `print_summary()` - Print statistical summary

### SystemMetrics

Data class containing metrics for a single point in time.

**Attributes:**
- `timestamp` - Unix timestamp
- `cpu_percent` - CPU usage (%)
- `memory_percent` - Memory usage (%)
- `memory_used_mb` - Used memory (MB)
- `memory_available_mb` - Available memory (MB)
- `gpu_utilization` - GPU usage (%) [optional]
- `gpu_memory_used_mb` - GPU memory used (MB) [optional]
- `gpu_memory_total_mb` - Total GPU memory (MB) [optional]
- `gpu_temperature` - GPU temperature (Â°C) [optional]

### MetricsPlotter

Class for creating visualizations from collected metrics.

**Constructor:**
```python
MetricsPlotter(metrics: List[SystemMetrics])
```

**Methods:**
- `plot_cpu_memory(ax=None)` - Plot CPU and memory usage
- `plot_gpu_utilization(ax=None)` - Plot GPU utilization
- `plot_gpu_memory(ax=None)` - Plot GPU memory usage
- `plot_gpu_temperature(ax=None)` - Plot GPU temperature
- `plot_all(figsize=(14, 10))` - Create complete dashboard
- `save_figure(filepath, dpi=150)` - Save plot to file
- `show()` - Display plot interactively

### quick_plot()

Convenience function for quick visualization.

```python
quick_plot(metrics, output_path=None, show=True)
```

## GPU Support

GPU monitoring requires:
1. NVIDIA GPU
2. NVIDIA drivers installed
3. `nvidia-ml-py` package (automatically installed with this package)

If no GPU is detected or GPU monitoring fails, the system will continue to work but without GPU metrics.

## Use Cases

### Profile ML Training

```python
from orchestr_ant_ion import SystemMonitor

monitor = SystemMonitor(interval=1.0)
monitor.start()

# Train your model
model.fit(X_train, y_train, epochs=10)
monitor.record()

monitor.stop()
monitor.print_summary()
```

### Monitor Long-Running Processes

```python
import time
from orchestr_ant_ion import SystemMonitor

monitor = SystemMonitor(interval=5.0)
monitor.start()

for batch in data_batches:
    process_batch(batch)
    monitor.record()
    time.sleep(1)

monitor.stop()
```

### Compare Different Implementations

```python
from orchestr_ant_ion import SystemMonitor, MetricsPlotter

# Implementation A
monitor_a = SystemMonitor()
monitor_a.start()
implementation_a()
monitor_a.stop()

# Implementation B
monitor_b = SystemMonitor()
monitor_b.start()
implementation_b()
monitor_b.stop()

# Compare
monitor_a.print_summary()
monitor_b.print_summary()
```

## Troubleshooting

### GPU Not Detected

If you have an NVIDIA GPU but it's not being detected:

1. Verify NVIDIA drivers are installed:
   ```bash
   nvidia-smi
   ```

2. Check if `nvidia-ml-py` is installed:
   ```bash
   pip list | grep nvidia-ml-py
   ```

3. Test GPU access:
   ```python
   import pynvml
   pynvml.nvmlInit()
   print(pynvml.nvmlDeviceGetCount())
   ```

### High Memory Usage

The monitor stores all metrics in memory. For long-running monitoring sessions:
- Use larger sampling intervals
- Periodically save and clear metrics
- Use a streaming/database approach for very long sessions

## License

Same as the main package.

