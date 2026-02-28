
# Example entry point demonstrating how to import modules.
# Adjust calls according to the actual function names moved from the notebook.
from thesis_package import imports as imp
from thesis_package import config as cfg
from thesis_package import io as io_mod
from thesis_package import geometry
from thesis_package import graph
from thesis_package import visualize
from . import processing, config

def main():
    processing.run_pipeline(config)
if __name__ == "__main__":
    main()
    print("thesis_package wired up. Customize main.py to call your pipeline.")
