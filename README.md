<div align="center">
  <a href="https://jonasheinle.de">
    <img src="images/logo.png" alt="logo" width="200" />
  </a>

  <h1>KataglyphisPythonInference</h1>

  <h4>👨🏻‍💻 Fast python AI prototyping 🐍</h4>
</div>

Docs can be found [here](https://pythoninference.jonasheinle.de/).

[![Ubuntu 24.04 Workflow](https://github.com/Kataglyphis/Kataglyphis-PythonInference/actions/workflows/ubuntu-24.04.yml/badge.svg)](https://github.com/Kataglyphis/Kataglyphis-PythonProjectTemplate/actions/workflows/ubuntu-24.04.yml)
[![Windows 2025 Workflow](https://github.com/Kataglyphis/Kataglyphis-PythonInference/actions/workflows/windows-2025.yml/badge.svg)](https://github.com/Kataglyphis/Kataglyphis-PythonProjectTemplate/actions/workflows/windows-2025.yml)
[![Ubuntu 24.04 ARM Workflow](https://github.com/Kataglyphis/Kataglyphis-PythonInference/actions/workflows/ubuntu-24.04-arm.yml/badge.svg)](https://github.com/Kataglyphis/Kataglyphis-PythonProjectTemplate/actions/workflows/ubuntu-24.04-arm.yml)
[![CodeQL](https://github.com/Kataglyphis/Kataglyphis-PythonInference/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/Kataglyphis/Kataglyphis-PythonProjectTemplate/actions/workflows/github-code-scanning/codeql)
[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/paypalme/JonasHeinle)
[![Twitter](https://img.shields.io/twitter/follow/Cataglyphis_?style=social)](https://twitter.com/Cataglyphis_)

## Table of Contents

- [About The Project](#about-the-project)
  - [Key Features](#key-features)
  - [Dependencies](#dependencies)
  - [Useful Tools](#useful-tools)
- [Overview](#overview)
- [Getting Started](#getting-started)
  - [Setup](#setup)
  - [Installation](#installation)
  - [Deployment Recommendations (Hardware/Software)](#deployment-recommendations-hardwaresoftware)
- [Tests](#tests)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Contact and Maintainers](#contact-and-maintainers)
- [Acknowledgements](#acknowledgements)
- [Literature](#literature)
- [Demo](#demo)
- [References](#references)
- [Known Issues](#known-issues)

---

## About The Project

This project is a starting point for my Python AI workloads.  
Use it as a starting point for creating and deploying your own Python projects.

### Key Features

- Features are to be adjusted to your own project needs.


<div align="center">


|            Category           |           Feature                             |  Implement Status  |
|-------------------------------|-----------------------------------------------|:------------------:|
|  **Packaging agnostic**       | Binary only deployment                        |         ✔️         |
|                               | Lore ipsum                                    |         ✔️         |
|  **Infrastructure**           |                                               |                     |
|                               | Add hydra support                             |         ❌         |
|  **Lore ipsum agnostic**      |                                               |                     |
|                               | Advanced unit testing                         |         🔶         |
|                               | Advanced performance testing                  |         🔶         |
|                               | Advanced fuzz testing                         |         🔶         |

</div>

**Legend:**
- ✔️ - completed  
- 🔶 - in progress  
- ❌ - not started


### Dependencies

- Adjust according to your project’s actual Python and library dependencies.

### Useful Tools

| Tool                                                    | Description             |
| ------------------------------------------------------- | ----------------------- |
| [ty](https://github.com/astral-sh/ty)                   | ty                      |
| [ruff](https://github.com/astral-sh/ruff)               | Linter                  |
| [uv](https://github.com/astral-sh/uv)                   | Command-line utility    |
| [kedro](https://kedro.org/)                             | Infrastructure          |
| [miniforge3](https://github.com/conda-forge/miniforge)  | Infrastructure          |
| [scalene](https://github.com/plasma-umass/scalene)      | Benchmarking            |
| [py-spy](https://github.com/benfred/py-spy)             | Benchmarking            |

---

## Overview

The versioning of the package can be viewed in [CHANGELOG.md](CHANGELOG.md).

---

## Getting Started

### Setup

Feel free to adjust for your own environment.
F.e. create a virtual venv with a specific python version.

```bash
uv venv
python3.10 -m venv .venv
```

### Pre commit hook
```bash
uv venv
source .venv/bin/activate # .venv/Scripts/activate on pwsh
uv pip install pre-commit
pre-commit install
# run on all files once (optional)
pre-commit run --all-files
```

### Installation

There are three major ways to install this package in your environment:

1. **Install directly via pip:**
   ```bash
   pip install KataglyphisPythonInference@git+https://github.com/Kataglyphis/KataglyphisPythonInference
   ```
   or install a specific tagged version:
   ```bash
   pip install KataglyphisPythonInference@git+https://github.com/Kataglyphis/KataglyphisPythonInference@v0.0.1
   ```

2. **Install after cloning the repo:**
   ```bash
   git clone https://github.com/Kataglyphis/KataglyphisPythonInference
   pip install .
   ```

   or

   ```bash
   pip install -e .
   ```

   (an editable install: changes in the repo will be reflected in your environment)

3. **Add as a submodule to your repository:**
   ```bash
   git submodule add https://github.com/Kataglyphis/KataglyphisPythonInference
   ```
   Make sure that all dependencies are installed during your repo’s installation.  
   (Not generally recommended, as it can be more complicated.)

#### Picamera web browser live stream

You need to shared system packages for the 
`picamera2` should be installed via `apt`
([source](https://github.com/raspberrypi/picamera2))

```bash
sudo apt install python3-picamera2
uv venv --system-site-packages
```

### Deployment Recommendations (Hardware/Software)

#### Python package deployment in pure C

For insights into deploying Python packages into production as “binary only” wheels
have a look into the corresponding workflows.  

After creating the wheel you can check content with the command:
```bash
# Unzip a .whl
python -m zipfile --extract <ZIP_DATEI> <ZIEL_ORDNER>
```

This will print all available compatible tags for deployment

```bash
pip debug --verbose
```

**__NOTE:__** If you want to install your package editable and you previously deployed  
it you will need to delete all Cython generated files first. You can use the following  
command for it:  
```bash  
find . -type f \( -name '\*.c' -o -name '\*.cpp' -o -name '\*.so' -o -name '\*.pyd' -o -name '\*.html' \) -delete  
```

Or on windows ... do this  
```powershell  
Get-ChildItem -Path . -Recurse -File | Where-Object { $\_.Extension -in '.c', '.cpp', '.so', '.pyd', '.html' } | Remove-Item  
```

## Tests

For development, you can install comprehensive dependencies with:
```bash
pip install -v -e .[dev,docs,test]
```
Then run your testing framework (e.g., `pytest`).

---

## Roadmap

Specify planned features or improvements here.

---

## Contributing

Contributions make open source software better! To contribute:

1. Fork the project
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## License

Use or adapt your license here.

---

## Contact and Maintainers

- Primary contact: [@Cataglyphis_](https://twitter.com/Cataglyphis_)
- Project example link: [GitHub](https://github.com/Kataglyphis/...)

**Maintainers:**  
Replace this text with the list of maintainers who can be asked and assigned to review or merge requests.

---

## Acknowledgements

Mention credits for any third-party resources.

---

## Literature

List helpful literature, tutorials, or references that have guided this project.

### Deployment
[Protect source code](https://art-vasilyev.github.io/posts/protecting-source-code/)
---

## Demo

If you have examples or demonstrations, add them here.

---

## References

KataglyphisPythonPackage is used in the following repos/packages:
- Adapt this list to reference actual uses.

---

## Known Issues

List any known issues here. 
