import timeit

from kataglyphispythonpackage.demo import SimpleMLPreprocessor


def run():
    ml = SimpleMLPreprocessor(10000)
    ml.run_pipeline()


if __name__ == "__main__":
    duration = timeit.timeit("run()", setup="from __main__ import run", number=5)
    print(f"Average runtime over 5 runs: {duration / 5:.4f} seconds")
