"""LanguageSpec for Vue single-file components."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="vue",
    display_name="Vue",
    extensions=frozenset({".vue"}),
    is_code=True,
    grammar_package=None,  # no direct tree-sitter grammar — re-parsed as TS/JS
    scm_file=None,
    entry_point_patterns=("app.vue", "main.vue", "index.vue", "error.vue"),
    manifest_files=("package.json",),
    lock_files=("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
    blocked_dirs=("node_modules", "dist", ".nuxt", ".output"),
    color_hex="#41b883",
)
