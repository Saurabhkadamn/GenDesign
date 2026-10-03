# Forma: commercial SaaS dependency and license review

Review date: 1 October 2026. Scope: the Forma application in `D:/v1`, its declared and locked dependencies, and the CAD components proposed in the capability research.

Implementation update, 2 October 2026: Forma now installs the locked CAD dependencies and pinned OndselSolver adapter/shared libraries in a qualified Linux snapshot. Actual installed versions and binary/source hashes are attested; the image contains solver corresponding source and LGPL/MIT notices, and all 45 runtime tests passed with network access denied. The former copy-only snapshot reproducibility gap described below is resolved for this qualified image. This does not complete an artifact-level SBOM, frontend notice review, cloud/model contract review or legal clearance. See [native assembly decision](FORMA_NATIVE_ASSEMBLY_BOM_DECISION.md) and [release evidence](FORMA_HOSTED_ASSEMBLY_ACCEPTANCE.json).

Release dependency update, 3 October 2026: Next.js and its matching lint/compiler packages are patched to 16.3.8, Undici to 7.29.1 and DOMPurify to 3.4.16. Their declared licenses remain MIT and, for DOMPurify, MPL-2.0 OR Apache-2.0. The exact lockfile delta, integrity hashes and production audit results are recorded in [FORMA_RELEASE_DEPENDENCY_DELTA.json](FORMA_RELEASE_DEPENDENCY_DELTA.json), which supplements the historical inventory below. The production dependency audit reports zero known advisories; the complete dependency audit retains 12 development advisories. Neither result is general security or license clearance.

## Decision

**The reviewed software stack appears compatible with a paid, proprietary, hosted Forma SaaS.** CadQuery and the other open-source components do not impose a general prohibition on commercial use. Forma can charge for its own application and engineering workflow while complying with the underlying licenses.

This is a preliminary dependency review, not clearance of the deployed product. The remaining work is to verify the actual CAD snapshot and native binaries, supply notices for distributed assets, and check the applicable cloud/model contracts. A desktop installer or customer-hosted image needs a separate distribution review.

The detailed package inventory is [FORMA_DEPENDENCY_LICENSE_INVENTORY.json](D:/v1/docs/FORMA_DEPENDENCY_LICENSE_INVENTORY.json). The engineering comparison is [FORMA_CAD_CAPABILITY_RESEARCH.md](D:/v1/docs/FORMA_CAD_CAPABILITY_RESEARCH.md).

## What was checked

- [Root package manifest](D:/v1/package.json), [web manifest](D:/v1/apps/web/package.json), and [JavaScript lockfile](D:/v1/package-lock.json).
- [API manifest](D:/v1/apps/api/pyproject.toml) and [API lockfile](D:/v1/apps/api/uv.lock).
- [CAD runtime manifest](D:/v1/runtimes/python/pyproject.toml) and [CAD runtime lockfile](D:/v1/runtimes/python/uv.lock).
- Exact-version PyPI license metadata for all 164 unique registry package/version pairs in the two Python lockfiles: 72 API entries and 104 runtime entries, with 12 shared pairs. All metadata requests succeeded.
- All 1,187 JavaScript lock entries, including workspace records, development dependencies, optional binaries, and alternative platforms. These counts are not counts of deployed dependencies.
- Local license files for packages with missing npm declarations, selected installed API metadata, and authoritative upstream licenses for the important CAD components.

No AGPL, SSPL, or non-commercial declaration was found in the package-level license expressions/classifiers reviewed. This does not prove that every bundled library, asset, model, container, or OS package has been cleared.

The two missing third-party npm declarations were resolved as MIT using the installed `khroma` and `webgl-constants` license files. The six remaining unspecified npm entries are Forma workspace records or workspace links. Python's `mypy-extensions` omitted license metadata; its [version 1.1.0 source license](https://github.com/python/mypy_extensions/blob/1.1.0/LICENSE) is MIT. No unresolved package-level third-party license gap remains from these checks; native binary composition remains outside that conclusion.

## Current components

“Yes” below means that the identified license allows commercial use subject to its conditions. It does not certify an entire native wheel, linked library set, hosted service account, or generated output.

| Component | Function in Forma | Reviewed license | Paid hosted SaaS | Conditions or outstanding evidence |
|---|---|---|---|---|
| CadQuery 2.8.0 | Python parametric CAD | Apache-2.0 | Yes | Preserve license and applicable notices for copies distributed; identify modifications when required. [Upstream](https://github.com/CadQuery/cadquery/blob/master/LICENSE). |
| cadquery-ocp / proxy 7.9.3.1.1 | Python bindings to the CAD kernel | Apache-2.0 at binding/package level | Yes | This does not replace the underlying OCCT license. |
| Open CASCADE / OCCT | B-rep solid and surface geometry, CAD exchange | LGPL-2.1 with Open CASCADE exception | Yes, with conditions | Exact kernel/build and bundled third-party code must be recorded. Distribution needs LGPL source/notices and an appropriate way to modify or replace the library; exception includes an OCCT acknowledgment condition. [Version 7.9.3 exception](https://github.com/Open-Cascade-SAS/OCCT/blob/V7_9_3/OCCT_LGPL_EXCEPTION.txt), [license](https://github.com/Open-Cascade-SAS/OCCT/blob/V7_9_3/LICENSE_LGPL_21.txt). |
| NumPy 2.2.6, SciPy 1.15.3, SymPy 1.14.0, Pint 0.24.4, mpmath 1.3.0 | Numerical computation, units, symbolic mathematics | BSD-family at project level | Yes | Native wheels can bundle additional libraries and compiler runtimes with separate notices/permissions. |
| CVXPY 1.6.6, Clarabel, OSQP, SCS | Optimization | Apache-2.0 / MIT at reviewed project level | Yes | Review actual selected solver binaries and any additional optional commercial solver. |
| CasADi 3.6.7 | Numerical optimization dependency | LGPL-3.0-or-later | Yes, with conditions | Bundled solver/plugins require their own license inventory. [CasADi documentation](https://web.casadi.org/docs/#obtaining-and-installing). |
| NLopt 2.11.0 | Optimization dependency | Python metadata says MIT; compiled library is build-dependent | Yes, with conditions | Upstream's default Luksan-enabled build is LGPL-2.1-or-later. A build without that code can be MIT. Verify the actual wheel, rather than trusting its MIT label alone. [Exact-version COPYING](https://github.com/stevengj/nlopt/blob/v2.11.0/COPYING), [Python packaging license](https://github.com/DanielBok/nlopt-python/blob/master/LICENSE). |
| ezdxf 1.4.4, trimesh 4.6.10 | DXF utilities and mesh handling | MIT | Yes | Normal notice preservation; their presence does not prove a complete drawing workflow. |
| VTK 9.6.2; Numba / llvmlite | Geometry/visualization dependency and numerical execution | BSD-family; llvmlite declares BSD-2-Clause AND Apache-2.0 WITH LLVM-exception | Yes at reviewed license level | Inventory bundled native libraries. |
| Matplotlib 3.10.3, nbclient, ipykernel | Plotting and notebook execution | PSF-style / BSD-family | Yes | Preserve relevant license files in distributions. |
| FastAPI, Pydantic, LangGraph, LangSmith SDK, Vercel Python SDK | API, contracts, agent orchestration, tracing, execution client | MIT at reviewed package level | Yes | A permissive SDK license does not grant free access to its hosted service. |
| Uvicorn, HTTPX, python-dotenv | API server and configuration | BSD-3-Clause | Yes | Notice obligations on redistribution. |
| cryptography 50.0.1 | Application cryptography | Apache-2.0 OR BSD-3-Clause | Yes | Honor the chosen license; native library notices also matter. |
| psycopg / psycopg-binary 3.2.10; psycopg-pool 3.3.1 | PostgreSQL access and checkpoint connection pooling | LGPLv3; pool LGPL-3.0-only | Yes, with conditions | Ordinary backend use does not require publishing Forma's application code. Shipping these libraries invokes distribution duties. |
| Next.js 16.3.3, React 19.2.8, Three.js 0.185.1, React Three Fiber/Drei, Radix UI, Tailwind | Web application and 3D viewer | MIT | Yes | Browser bundles are delivered to users; preserve required notices. |
| AI SDK 7.0.85, Streamdown 2.6.0 | AI UI and streaming utilities | Apache-2.0 | Yes | Include required license/NOTICE information for distributed code. |
| Lucide React 1.37.0 | Icons | ISC | Yes | Keep copyright/permission notices with distributed material. |
| Geist 1.7.2 | Font assets | SIL Open Font License 1.1 | Yes | Include font license/copyright; observe reserved-name rules for modified fonts. Font obligations do not make the rest of Forma open source. [OFL FAQ](https://openfontlicense.org/ofl-faq/). |
| sharp and libvips binary packages | Image processing dependency | sharp Apache-2.0; libvips LGPL-3.0-or-later; some platform packages combine licenses | Yes, with conditions | Which optional binary ships depends on the build platform. Check the actual image/build rather than counting every optional lock entry as deployed. |
| Lightning CSS, certifi, orjson portions and other MPL entries | Build tooling, certificates, serialization | MPL-2.0 or mixed expressions | Yes, with conditions | Distributed covered files need source availability; this is file-level copyleft, not an obligation to publish all Forma code. `orjson` declares MPL-2.0 AND (Apache-2.0 OR MIT). [Mozilla FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/). |
| caniuse-lite | Browser-support data, mainly tooling | CC-BY-4.0 | Yes | Preserve required attribution when redistributing the dataset. |
| Other locked packages and development tools | Transitive dependencies, builds, tests | See JSON inventory | Package-level licenses reviewed | MIT, BSD, ISC, Apache and other permissive entries still carry their own notice conditions. Actual release inclusion needs a build inventory. |

CasADi is **not safely excluded from Linux** by the Windows-only direct requirement: the locked CadQuery package also depends on CasADi without that marker. The manifest marker only limits Forma's explicit direct pin. The actual sandbox installation still needs inspection.

## Hosted use, downloads and proprietary source

For ordinary GPL/LGPL software, backend execution without conveying software to users generally does not create the public-source requirement associated with distribution. AGPL adds a remote-network source-offer requirement for modified covered programs; how integration affects the covered work must be reviewed separately. Neither license is a general ban on charging money. [GNU FAQ](https://www.gnu.org/licenses/gpl-faq.en.html), [AGPL section 13](https://www.gnu.org/licenses/agpl-3.0.html).

Client-side JavaScript, WebAssembly and font files are delivered to users. SaaS therefore does not exempt the whole product from distribution obligations. A downloadable runtime, desktop client, customer Docker image, or on-premises package also changes the analysis. Dynamic linking can help LGPL compliance, but does not by itself discharge source, notice, replacement/relinking and recipient-rights obligations. Mozilla explicitly distinguishes server execution from client code delivery. [Mozilla FAQ, questions 16–17](https://www.mozilla.org/en-US/MPL/2.0/FAQ/).

**Models and drawings do not become open source merely because a copyleft CAD engine generated them.** A STEP, STL or DXF file describing the user's geometry is different from a copy of the engine's code. Output that embeds licensed source, artwork, fonts, templates or catalog content can have separate conditions. Supplier models and AI-generated code also need their own rights analysis. [GNU output FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#WhatCaseIsOutputGPL).

## Hosted services are a separate contract layer

| Service in Forma | SaaS consideration |
|---|---|
| Vercel hosting, Functions/Workflows, Sandbox and AI Gateway | Use an eligible commercial plan. Vercel expressly restricts Hobby to non-commercial personal use; its fair-use policy requires Pro or Enterprise for commercial usage. The account plan and any negotiated agreement were not inspected. [Fair-use policy](https://vercel.com/docs/limits/fair-use-guidelines). |
| Supabase hosted Auth, PostgreSQL and Storage | Cloud rights are governed by the applicable terms/order, separately from source-code licenses. Confirm the agreement covers the intended end-user application use and customer data processing. This review does not treat Supabase's entire self-hosted distribution as one MIT dependency. [Cloud terms](https://supabase.com/terms). |
| LangSmith hosted tracing | The MIT SDK does not license the hosted service. Check subscription, retention, permitted access and treatment of proprietary design data under the applicable agreement. [Service terms](https://www.langchain.com/terms-of-service). |
| OpenRouter or AI Gateway model access | Review the selected model/provider and service terms, commercial application rights, end-user terms, data handling and metering. Availability of a free endpoint does not establish unrestricted commercial rights. [OpenRouter terms](https://openrouter.ai/terms), [Vercel terms](https://vercel.com/legal/terms). |

The OpenAI-compatible adapter is an API format choice. It does not establish that Forma uses an OpenAI account, or that every compatible provider has the same contractual rights.

## Proposed CAD extensions: separate from the current stack

These are candidates from the engineering report, not a claim that they are deployed in Forma. Recheck the exact version, fork, module and binary selected before adoption.

| Candidate | Reviewed upstream license situation | Commercial SaaS decision |
|---|---|---|
| build123d | Apache-2.0; kernel dependencies retain their own licenses | Compatible with proprietary SaaS, with notices and dependency review. [License](https://github.com/gumyr/build123d/blob/dev/LICENSE). |
| FreeCAD / TechDraw | LGPL-family; module/dependency terms must be inventoried | Commercial use allowed; distribution requires component-specific compliance. [FreeCAD licensing documentation](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/License.md). |
| FreeCAD/OndselSolver | Current LICENSE LGPL-2.1 | Commercial use allowed subject to LGPL. [License](https://github.com/FreeCAD/OndselSolver/blob/main/LICENSE). |
| shaise/FreeCAD_SheetMetal | Current LICENSE LGPL-2.1; old versions/forks and README wording can differ | Current candidate can support commercial use under LGPL; pin and inspect the chosen release. [License](https://github.com/shaise/FreeCAD_SheetMetal/blob/master/LICENSE). |
| SolveSpace / libslvs | GPL-3.0-family | Commercial use is allowed. Proprietary distribution/linking needs careful review; ordinary server execution is different. [Project](https://github.com/solvespace/solvespace). |
| Gmsh | GPL, with an upstream commercial licensing option | Upstream explicitly directs closed-source integrations to commercial licensing. Resolve the intended integration and rights before selecting it. [Official licensing statement](https://gmsh.info/). |
| CalculiX | GPL-2.0-or-later | Can be used commercially; assess covered modifications and any distributed combination. [Official site](https://www.calculix.de/). |
| OpenCAMLib | Current LGPL-2.1-family; older releases differed | Commercial use permitted subject to the chosen release's terms. [Current license](https://github.com/aewallin/opencamlib/blob/master/COPYING). |
| Parasolid, CGM, proprietary native-format translators or solver plugins | Vendor commercial agreements | Need explicit rights for hosted execution, tenancy, redistribution and any usage-based royalties. Open-source permission for a wrapper cannot supply those rights. |

## Concrete remaining work before calling the release compliant

1. **Inventory the actual release.** Record frontend/server bundles, exact API environment, CAD snapshot packages, shared libraries, OS packages and plugin assets; include versions, hashes, licenses and provenance. Generate SPDX or CycloneDX SBOMs from those artifacts. This review's JSON is a metadata inventory, not an artifact SBOM.
2. **Inspect native license bundles.** Prioritize OCCT/OCP, NLopt's Luksan build setting, CasADi's solver plugins, VTK, libvips, psycopg-binary, scientific wheels and compiler runtimes. Retain exact corresponding source and license evidence for any distribution plan.
3. **Build third-party notices.** Publish an accessible notices page and include the applicable notices/licenses in distributed bundles and download packages; supply covered-source access where required. Do not strip notices during minification.
4. **Record cloud/model rights.** Confirm commercial hosting eligibility and the actual subscription/order/provider terms used in production. Record those terms alongside the selected model and service configuration.
5. **Keep upgrade reviews repeatable.** Diff the license inventory in CI when lockfiles, plugins, fonts, models or native images change. Flag AGPL, SSPL, non-commercial, source-available and unspecified entries for a deliberate decision rather than assuming package installation means permission.
6. **Define distribution and ownership.** Treat on-premises/desktop shipping as a new release review. Confirm Forma owns or has rights to its own contributed code, engineering templates and parts library; `private: true` is a package setting, not ownership evidence.

At the initial review, [create_runtime_snapshot.py](D:/v1/scripts/create_runtime_snapshot.py) copied runtime files and a lockfile without installing the locked dependencies. That historical gap explains why the repository lockfile alone was insufficient. The script now delegates to [qualify_runtime_snapshot.py](../scripts/qualify_runtime_snapshot.py), which installs, attests and tests the actual selected image. Broader bundled-library and distribution obligations still need the release-specific inventory described above.

The initial license research made no code or deployment changes; later implementation and qualification are recorded in the update above. Retain CadQuery/OCCT for the hosted SaaS, finish the release inventory and notice work, and evaluate future CAD/simulation modules individually. License compatibility does not establish engineering reliability, accuracy or enterprise readiness; those remain the separate acceptance gates in the capability research.
