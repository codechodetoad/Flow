import matplotlib.pyplot as plt
import numpy as np

# Input sizes
input_sizes = [10, 100, 1000, 10000, 100000, 1000000, 10000000]

# Time data (converted to milliseconds for consistency)
bubble_sort_times = [0.00027, 0.0274, 2.7, 27.4, 27383, 27383 * 60 / 1000, 76 * 60 * 1000]  # ms
quick_sort_times = [0.0009, 0.0091, 0.177, 1.149, 10, 131, 1520]
insertion_sort_times = [0.00005, 0.0054, 0.54, 53, 5383, 9 * 60 * 1000, 15 * 60 * 60 * 1000]  # ms

# Plotting
plt.figure(figsize=(10, 6))
plt.plot(input_sizes, bubble_sort_times, marker='o', label='Bubble Sort')
plt.plot(input_sizes, quick_sort_times, marker='s', label='Quick Sort')
plt.plot(input_sizes, insertion_sort_times, marker='^', label='Insertion Sort')

plt.xscale('log')
plt.yscale('log')
plt.xlabel('Input Size (log scale)')
plt.ylabel('Time (ms, log scale)')
plt.title('Sorting Algorithm Time Complexity (log-log scale)')
plt.legend()
plt.grid(True, which='both', linestyle='--', linewidth=0.5)
plt.tight_layout()
plt.show()
