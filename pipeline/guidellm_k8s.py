#!/usr/bin/env python3
"""Small Kubernetes helpers shared by the GuideLLM scenario runner."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

GUIDELLM_IMAGE = (
    "ghcr.io/vllm-project/guidellm@"
    "sha256:38a831882f454f22320644030122ba4f78c05fe57a8512b2ca37ac364a2e00c1"
)


def command(
    args: list[str], *, input_text: str | None = None, check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, input=input_text, text=True, capture_output=True, check=check,
    )


def kubectl(
    namespace: str, args: list[str], **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    return command(["kubectl", "-n", namespace, *args], **kwargs)


def resource_name(prefix: str, tenant: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", f"{prefix}-{tenant}".lower()).strip("-")
    return value[:63].rstrip("-")


def backend_config(
    endpoint: str, model: str, tenant: dict[str, Any], *, http2: bool = True,
    connection_close: bool = False,
) -> dict[str, Any]:
    headers = {
        "x-llm-d-inference-fairness-id": tenant["fairness_id"],
        "x-llm-d-inference-objective": tenant["objective"],
        "x-gateway-inference-fairness-id": tenant["fairness_id"],
        "x-gateway-inference-objective": tenant["objective"],
    }
    if connection_close:
        headers["Connection"] = "close"
    return {
        "kind": "openai_http", "target": endpoint, "model": model,
        "request_format": "/v1/completions", "stream": True, "http2": http2,
        "validate_backend": False, "extras": {"headers": headers},
    }


def job_document(
    namespace: str, name: str, trace_configmap: str, fixed_epoch: float,
    backend: dict[str, Any], active_deadline_s: int, worker_processes: int = 10,
    drain_after_done: bool = False,
    recover_multiline_sse: bool = False,
    extra_env: dict[str, str] | None = None,
    poll_interval_s: float = 0.01,
) -> dict[str, Any]:
    document = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "flow-control-guidellm", "managed-by": "benchmark-runner"},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": active_deadline_s,
            "template": {
                "metadata": {"labels": {"app": "flow-control-guidellm", "run": name}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [{
                        "name": "guidellm",
                        "image": GUIDELLM_IMAGE,
                        "command": ["/bin/sh", "-c"],
                        "args": [
                            "python /config/guidellm_fixed_start.py run --disable-console; "
                            "code=$?; echo $code > /tmp/exit-code; touch /tmp/done; "
                            "while [ ! -e /tmp/collected ]; do sleep 1; done; exit $code",
                        ],
                        "env": [
                            {"name": "USER", "value": "guidellm"},
                            {"name": "HOME", "value": "/tmp"},
                            {"name": "HF_HOME", "value": "/tmp/hf"},
                            {"name": "TOKENIZERS_PARALLELISM", "value": "false"},
                            {"name": "GUIDELLM_FIXED_START_EPOCH", "value": f"{fixed_epoch:.6f}"},
                            {"name": "GUIDELLM__MAX_WORKER_PROCESSES", "value": str(worker_processes)},
                            {"name": "GUIDELLM__MP_POLL_INTERVAL", "value": str(poll_interval_s)},
                            {"name": "GUIDELLM_DRAIN_AFTER_DONE", "value": "1" if drain_after_done else "0"},
                            {"name": "GUIDELLM_RECOVER_MULTILINE_SSE", "value": "1" if recover_multiline_sse else "0"},
                            {"name": "GUIDELLM__SPEC__BACKEND", "value": json.dumps(backend)},
                            {"name": "GUIDELLM__SPEC__PROFILE", "value": '{"kind":"replay","time_scale":1.0}'},
                            {"name": "GUIDELLM__SPEC__CONSTRAINTS", "value": "[]"},
                            {"name": "GUIDELLM__SPEC__DATA", "value": '[{"kind":"trace_synthetic","path":"/config/trace.jsonl"}]'},
                            {"name": "GUIDELLM__SPEC__OUTPUTS", "value": '[{"kind":"json","path":"/tmp/raw.json"}]'},
                        ],
                        "resources": {
                            "requests": {"cpu": "4", "memory": "4Gi"},
                            "limits": {"cpu": "4", "memory": "8Gi"},
                        },
                        "volumeMounts": [{"name": "config", "mountPath": "/config"}],
                    }],
                    "volumes": [{
                        "name": "config", "configMap": {"name": trace_configmap},
                    }],
                },
            },
        },
    }
    container = document["spec"]["template"]["spec"]["containers"][0]
    container["env"].extend(
        {"name": name, "value": value}
        for name, value in sorted((extra_env or {}).items())
    )
    return document
