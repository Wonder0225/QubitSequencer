# Third-party components in the Windows 0.2.1 bundle

Qubit Sequencer uses the following third-party software. These components retain
their own copyrights and licenses independently of the application's license.
The bundled license texts are in `licenses/third-party/`.

| Component | Version | License / upstream |
| --- | --- | --- |
| Python | 3.12.7 | PSF license and notices in `Python-LICENSE.txt`; [source](https://www.python.org/downloads/release/python-3127/) |
| PySide6 and Shiboken6 | 6.9.2 | LGPL-3.0; [source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.9.2-src/) |
| Qt libraries | 6.9.2 | LGPL-3.0; [source archives](https://download.qt.io/archive/qt/6.9/6.9.2/submodules/), [Qt notices](https://doc.qt.io/qt-6/licenses-used-in-qt.html) |
| openpyxl | 3.1.5 | MIT; [source](https://pypi.org/project/openpyxl/3.1.5/) |
| lxml | 5.2.1 | BSD and the incorporated libxml2/libxslt notices; [source](https://pypi.org/project/lxml/5.2.1/) |
| Pillow | 10.4.0 | HPND and incorporated library notices; [source](https://pypi.org/project/pillow/10.4.0/) |
| setuptools | 80.9.0 | MIT; [source](https://pypi.org/project/setuptools/80.9.0/) |
| PyInstaller bootloader | 6.22.3 | GPL with the bootloader distribution exception; [source and license](https://pyinstaller.org/en/stable/license.html) |

The Qt/PySide6 shared libraries are distributed separately in `_internal/`; they
have not been modified by this project. The build instructions in `README.md`
describe how to rebuild the application with compatible dependencies.

The Qt sources and their third-party notices are maintained by their upstream
projects. See the linked versioned source archives and the [Qt for Python license
documentation](https://doc.qt.io/qtforpython-6/licenses.html). Keep the bundled
notices and licenses when redistributing this package.
