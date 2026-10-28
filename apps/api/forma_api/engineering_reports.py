"""Downloadable reports composed from immutable receipts, not model-written numbers."""

import csv
from hashlib import sha256
import io
import json
import math

from pydantic import Field

from .contracts import Contract
from .execution import digest, identity
from . import db
from .project_record import append_record


class ComparisonRow(Contract):
    label: str = Field(min_length=1, max_length=300)
    calculationRecordId: str
    calculationValue: str | None = None
    measurementRecordId: str
    tolerance: float = Field(ge=0, allow_inf_nan=False)
    toleranceSourceId: str


class CreateEngineeringReports(Contract):
    candidateHash: str
    intervalRecordIds: list[str] = Field(default_factory=list)
    calculationRecordIds: list[str] = Field(default_factory=list)
    comparisons: list[ComparisonRow] = Field(default_factory=list)


def csv_bytes(columns, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)

    # A report may contain untrusted user/component names. Do not turn those into
    # executable spreadsheet formulae when the CSV is opened in an office app.
    def cell(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    writer.writerows([[cell(row.get(column, "")) for column in columns] for row in rows])
    return output.getvalue().encode("utf-8")


def compose_reports(snapshot, state, request, records):
    expected = identity(snapshot, state.get("requirements") or [])
    if (
        request.candidateHash != expected["candidate"]
        or (state.get("validation") or {}).get("identity") != expected
    ):
        raise ValueError("Reports require independent validation for the exact current candidate")

    def evidence(record_id):
        row = records[record_id]
        if row.get("kind") != "evidence" or row.get("candidate_hash") != expected["candidate"]:
            raise ValueError("Report refers to missing or stale evidence")
        return row["body"]

    intervals = []
    for record_id in request.intervalRecordIds:
        body = evidence(record_id)
        if body.get("definition", {}).get("type") == "interval":
            if body.get("identity") != expected or body.get("planVersion") != (
                state.get("acceptance_plan") or {}
            ).get("version"):
                raise ValueError("Tolerance report refers to a superseded requirement check")
            body = body["result"]
        if (
            not {"formula", "nominal", "minimum", "maximum", "unit", "status", "inputs"}
            <= body.keys()
        ):
            raise ValueError("Tolerance reports require an interval-calculation receipt")
        intervals.append({"recordId": record_id, **body})
    comparisons = []
    for row in request.comparisons:
        calculation = evidence(row.calculationRecordId)
        if row.calculationValue is None:
            predicted, unit = calculation.get("nominal"), calculation.get("unit")
        else:
            result = calculation.get("result", {}).get("results", {}).get(row.calculationValue, {})
            predicted, unit = result.get("value"), result.get("unit")
        measurement = evidence(row.measurementRecordId).get("measurement", {})
        measured = measurement.get("value")
        if measurement.get("identity") != expected or not unit or measurement.get("unit") != unit:
            raise ValueError(
                "Comparison requires current geometry evidence and matching explicit units"
            )
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in (predicted, measured)
        ):
            raise ValueError(
                "Comparison requires finite scalar values from recorded calculations and measurements"
            )
        source = records[row.toleranceSourceId]
        if source.get("kind") != "source" or source["body"].get("role") == "assistant":
            raise ValueError("Comparison tolerance needs an original source reference")
        comparisons.append(
            {
                **row.model_dump(),
                "calculated": predicted,
                "measured": measured,
                "unit": unit,
                "difference": measured - predicted,
                "withinComparisonTolerance": abs(measured - predicted) <= row.tolerance,
                "configurationId": measurement.get("request", {}).get("configurationId"),
                "interpretation": "Numeric agreement only; no certification of the chosen formula or tolerance.",
            }
        )
    cells = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "# Engineering calculation record\n",
                "This notebook preserves executed calculation source and its receipts. Reproducibility does not establish engineering correctness.\n",
                "Candidate: " + expected["candidate"] + "\n",
            ],
        }
    ]
    for record_id in request.calculationRecordIds:
        body = evidence(record_id)
        source = snapshot["files"].get(body.get("sourcePath"))
        if (
            not source
            or digest(source) != body.get("sourceHash")
            or not body.get("ok")
            or not body.get("reproducible")
        ):
            raise ValueError(
                "Notebook requires a reproducible calculation receipt matching the saved source"
            )
        cells.append(
            {
                "cell_type": "code",
                "metadata": {"formaEvidenceRecordId": record_id},
                "source": source.splitlines(keepends=True) + ["\ncalculate()\n"],
                "execution_count": None,
                "outputs": [
                    {
                        "output_type": "display_data",
                        "metadata": {},
                        "data": {"application/json": body["result"]},
                    }
                ],
            }
        )
    cells.append(
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": [
                "## Tolerance interval receipts\n",
                "```json\n",
                json.dumps(intervals, indent=2, allow_nan=False),
                "\n```\n",
            ],
        }
    )
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "forma": {"identity": expected, "reexecutionRequiresQualifiedRuntime": True},
        },
        "cells": cells,
    }
    for i, cell in enumerate(cells):
        cell["id"] = "receipt-" + str(i)
    report = {
        "identity": expected,
        "acceptancePlan": state.get("acceptance_plan"),
        "requirements": state["validation"]["report"].get("requirements", []),
        "configurationExports": state["validation"]["report"].get("configurationExports", []),
        "intervals": intervals,
        "comparisons": comparisons,
        "limitations": [
            "Execution and numerical agreement are not engineering acceptance.",
            "Unsupported checks and physical performance remain unverified.",
        ],
    }
    definitions = {c["id"]: c for c in snapshot["manifest"]["components"]}
    inventory = [
        {
            "instanceId": i["id"],
            "componentId": i["definitionId"],
            "name": i["name"],
            "parentId": i.get("parentId") or "",
            "kind": definitions[i["definitionId"]]["kind"],
        }
        for i in snapshot["manifest"]["instances"]
    ]
    outputs = {
        "engineering-report.json": (
            "report",
            json.dumps(report, indent=2, allow_nan=False).encode(),
        ),
        "tolerance-chains.csv": (
            "report",
            csv_bytes(
                [
                    "recordId",
                    "formula",
                    "nominal",
                    "minimum",
                    "maximum",
                    "unit",
                    "requiredMinimum",
                    "requiredMaximum",
                    "status",
                ],
                intervals,
            ),
        ),
        "measured-versus-calculated.csv": (
            "report",
            csv_bytes(
                [
                    "label",
                    "calculated",
                    "measured",
                    "unit",
                    "difference",
                    "tolerance",
                    "withinComparisonTolerance",
                    "configurationId",
                    "calculationRecordId",
                    "measurementRecordId",
                ],
                comparisons,
            ),
        ),
        "component-inventory.csv": (
            "report",
            csv_bytes(["instanceId", "componentId", "name", "parentId", "kind"], inventory),
        ),
    }
    # Do not produce an apparently executed calculation notebook when no
    # executable mathematics was run. Interval-only analysis still has its report.
    if request.calculationRecordIds:
        outputs["calculations.ipynb"] = (
            "notebook",
            json.dumps(notebook, indent=2, allow_nan=False).encode(),
        )
    return outputs, report


async def create_reports(run, snapshot, state, request):
    from .repositories.projects import identifier

    ids = set(request.intervalRecordIds + request.calculationRecordIds)
    for row in request.comparisons:
        ids.update([row.calculationRecordId, row.measurementRecordId, row.toleranceSourceId])
    records = {}
    for record_id in ids:
        records[record_id] = await db.one(
            "project_records",
            {"id": f"eq.{identifier(record_id)}", "project_id": f"eq.{run['project_id']}"},
        )
    outputs, report = compose_reports(snapshot, state, request, records)
    artifacts = []
    for name, (kind, content) in outputs.items():
        content_hash = sha256(content).hexdigest()
        path = f"{run['owner_id']}/{run['project_id']}/{run['id']}/{request.candidateHash}/reports/{content_hash}/{name}"
        await db.insert(
            "artifact_staging",
            {
                "storage_path": path,
                "run_id": run["id"],
                "project_id": run["project_id"],
                "bytes": len(content),
            },
            conflict="storage_path",
        )
        await db.storage(
            "object/cad-private/" + db.object_path(path),
            "POST",
            content=content,
            content_type="text/csv" if name.endswith(".csv") else "application/json",
        )
        artifacts.append(
            {
                "name": name,
                "kind": kind,
                "bytes": len(content),
                "storagePath": path,
                "contentHash": content_hash,
                "componentId": None,
            }
        )
    await append_record(
        run,
        "evidence",
        "reports:" + digest({"report": report, "request": request.model_dump()}),
        {"identity": report["identity"], "artifacts": artifacts, "report": report},
        candidate_hash=request.candidateHash,
    )
    return {
        "ok": True,
        "reportArtifacts": artifacts,
        "reportIdentity": report["identity"],
        "instruction": "Report files are available for publication. Their availability does not pass engineering requirements.",
    }
