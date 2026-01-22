"""Example script demonstrating system monitoring and plotting functionality.

This script shows how to:
1. Monitor system resources (CPU, RAM, GPU)
2. Log metrics during computation
3. Visualize the collected metrics
"""

from __future__ import annotations

import sys
import time

import numpy as np
from loguru import logger

from kataglyphispythoninference.metrics_plotter import MetricsPlotter, quick_plot
from kataglyphispythoninference.system_monitor import SystemMonitor


def simulate_workload(duration: float = 5.0) -> None:
    """Simulate a computational workload.

    Args:
        duration: How long to run the simulation (seconds)
    """
    logger.info(f"Starting workload simulation for {duration} seconds...")

    rng = np.random.default_rng()
    start = time.time()
    iteration = 0

    while time.time() - start < duration:
        # Simulate some CPU work
        _ = rng.random((1000, 1000)) @ rng.random((1000, 1000))

        # Allocate some memory
        data = rng.random((500, 500))
        _ = data.mean()

        iteration += 1
        if iteration % 10 == 0:
            logger.debug(f"Workload iteration {iteration}")

        time.sleep(0.1)

    logger.info(f"Workload simulation completed after {iteration} iterations")


def example_basic_monitoring() -> SystemMonitor:
    """Basic example: Monitor system during a workload."""
    logger.info("=" * 60)
    logger.info("EXAMPLE 1: Basic System Monitoring")
    logger.info("=" * 60)

    # Create monitor with 0.5 second sampling interval
    monitor = SystemMonitor(interval=0.5)

    # Start monitoring
    monitor.start()

    # Simulate workload and record metrics
    duration = 10.0
    start = time.time()

    while time.time() - start < duration:
        simulate_workload(duration=2.0)
        monitor.record()  # Record snapshot after each workload burst

    # Stop monitoring
    monitor.stop()

    # Print summary
    monitor.print_summary()

    return monitor


def example_with_plotting() -> None:
    """Example with visualization: Monitor and plot metrics."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("EXAMPLE 2: System Monitoring with Plotting")
    logger.info("=" * 60)

    # Create and start monitor
    monitor = SystemMonitor(interval=0.3)
    monitor.start()

    # Collect metrics during workload
    logger.info("Running workload and collecting metrics...")
    for _i in range(20):
        simulate_workload(duration=0.5)
        monitor.record()

    monitor.stop()
    monitor.print_summary()

    # Create visualizations
    logger.info("")
    logger.info("Creating visualizations...")

    metrics = monitor.get_metrics()
    plotter = MetricsPlotter(metrics)

    # Plot all metrics
    plotter.plot_all()

    # Save to file
    output_path = "output/system_metrics.png"
    plotter.save_figure(output_path)

    logger.info(f"Visualization saved to: {output_path}")
    logger.success("Example completed successfully!")

    # Optionally show interactive plot


def example_continuous_monitoring() -> None:
    """Example: Continuous monitoring with periodic recording."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("EXAMPLE 3: Continuous Background Monitoring")
    logger.info("=" * 60)

    monitor = SystemMonitor(interval=0.5)
    monitor.start()

    # Simulate different workload phases
    phases = [
        ("Idle phase", 3.0, 0.0),
        ("Light load", 3.0, 0.5),
        ("Heavy load", 3.0, 2.0),
        ("Cool down", 3.0, 0.0),
    ]

    for phase_name, phase_duration, workload_duration in phases:
        logger.info(f"Phase: {phase_name}")
        start = time.time()

        while time.time() - start < phase_duration:
            if workload_duration > 0:
                simulate_workload(duration=workload_duration)
            else:
                time.sleep(0.5)

            monitor.record()

    monitor.stop()
    monitor.print_summary()

    # Quick plot with convenience function
    quick_plot(
        monitor.get_metrics(),
        output_path="output/continuous_monitoring.png",
        show=False,
    )

    logger.success("Continuous monitoring example completed!")


def main() -> None:
    """Run all examples."""
    # Configure logger
    logger.remove()
    logger.add(
        sink=sys.stdout,
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    )

    logger.info("System Monitoring Examples")
    logger.info("=" * 60)

    # Run examples
    try:
        example_basic_monitoring()
        example_with_plotting()
        example_continuous_monitoring()

        logger.success("")
        logger.success("All examples completed successfully!")
        logger.info("Check the 'output/' directory for generated plots.")

    except KeyboardInterrupt:
        logger.warning("Examples interrupted by user")
    except Exception as e:
        logger.error(f"Error during examples: {e}")
        raise


if __name__ == "__main__":
    main()
