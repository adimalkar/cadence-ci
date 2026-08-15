"""The no-install corpus: public repos with visibly busy CI, ingested continuously from
week 1 -- history cannot be recovered once GitHub's 90-day log retention passes.

Deliberately distinct from `docs/HELDOUT.md`. These repos are looked at freely and used
to build and tune every detector; the held-out set never is.
"""

CORPUS: list[tuple[str, str]] = [
    # JS / TS ecosystem
    ("facebook", "react"),
    ("microsoft", "TypeScript"),
    ("denoland", "deno"),
    ("oven-sh", "bun"),
    ("nodejs", "node"),
    ("vitejs", "vite"),
    ("rollup", "rollup"),
    ("webpack", "webpack"),
    ("babel", "babel"),
    ("eslint", "eslint"),
    ("vuejs", "core"),
    ("angular", "angular"),
    ("remix-run", "remix"),
    ("solidjs", "solid"),
    ("jestjs", "jest"),
    ("microsoft", "vscode"),
    ("sveltejs", "kit"),
    # Rust
    ("rust-lang", "cargo"),
    ("clap-rs", "clap"),
    ("serde-rs", "serde"),
    ("actix", "actix-web"),
    ("tauri-apps", "tauri"),
    # Go
    ("gohugoio", "hugo"),
    ("gin-gonic", "gin"),
    ("cli", "cli"),
    ("hashicorp", "terraform"),
    ("prometheus", "prometheus"),
    ("etcd-io", "etcd"),
    ("docker", "compose"),
    ("moby", "moby"),
    # Python
    ("django", "django"),
    ("pallets", "flask"),
    ("psf", "requests"),
    ("pytest-dev", "pytest"),
    ("python", "cpython"),
    ("numpy", "numpy"),
    ("scikit-learn", "scikit-learn"),
    ("pytorch", "pytorch"),
    ("huggingface", "transformers"),
    ("fastapi", "fastapi"),
    ("encode", "django-rest-framework"),
    # JVM / mixed
    ("apache", "spark"),
    ("elastic", "elasticsearch"),
    # Systems / other
    ("redis", "redis"),
    ("ziglang", "zig"),
    ("temporalio", "temporal"),
    ("curl", "curl"),
    ("openssl", "openssl"),
    # already in the dev corpus from Phase 0 -- kept here so corpus-seed covers them too
    ("astral-sh", "ruff"),
    ("astral-sh", "uv"),
    ("prettier", "prettier"),
]
