# Drawing dependency delta

The drawing runtime adds exact pins `ezdxf==1.4.4` and `reportlab==5.0.1` to the existing Python lockfile. CadQuery/OpenCascade, native Ondsel and existing fonts retain their separately recorded dependencies and obligations. The qualified runtime records actual installed package versions and source identity.

| Dependency | Purpose | License evidence retained in installed distribution |
| --- | --- | --- |
| ezdxf 1.4.4 | DXF document and native dimension entities | MIT; `ezdxf-1.4.4.dist-info/licenses/LICENSE` |
| ReportLab 5.0.1 | Vector PDF export | BSD; `reportlab-5.0.1.dist-info/licenses/LICENSE` |
| Existing Matplotlib DejaVu Sans | Embedded PDF text; GD&T marks are drawn as vectors | `matplotlib/mpl-data/fonts/ttf/LICENSE_DEJAVU`, alongside Matplotlib's distribution license |

Package sources: [ezdxf 1.4.4](https://pypi.org/project/ezdxf/1.4.4/), [ReportLab 5.0.1](https://pypi.org/project/reportlab/5.0.1/), [DejaVu font license](https://dejavu-fonts.github.io/License.html). Preserve package/font notices in redistributed runtime images. This is a dependency delta and observed license inventory, not a complete artifact SBOM or legal clearance. The existing native kernel/solver notices and source bundle remain part of the runtime.
