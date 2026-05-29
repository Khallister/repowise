"""Dynamic-hint extractor for Vue SFC <template> component references.

Why this exists
===============
Vue 3 projects (especially Nuxt.js) auto-import components from the
``components/`` directory — no explicit ``import`` statement appears in
``<script setup>``.  The two-phase AST parser already captures explicit
script-block imports; this extractor closes the gap for auto-imported
and globally-registered components whose only trace is their tag name
inside ``<template>``.

Design
======
Two-pass regex scan over ``*.vue`` files:

1. Build a ``PascalCaseName → [repo-relative-path]`` index from every
   ``.vue`` filename in the repo.  ``MyButton.vue`` → ``"MyButton"``;
   ``base-card.vue`` → ``"BaseCard"`` (kebab stems are normalised).
2. For each ``.vue`` file, extract the ``<template>`` block body and
   scan it for component tags:

   * PascalCase tags: ``<MyButton``, ``<RouterView``
   * kebab-case tags containing at least one hyphen: ``<base-card``

   Both forms are normalised to PascalCase before the index lookup so a
   file named ``MyButton.vue`` is found whether the caller writes
   ``<MyButton>`` or ``<my-button>``.

3. Built-in Vue special elements (``<Transition>``, ``<KeepAlive>`` etc.)
   are excluded.  Library components (Vuetify's ``<VBtn>``, router's
   ``<RouterView>``) produce no edge because they have no matching
   ``.vue`` file in the repo — the lookup silently returns nothing.

No HTML parser is used — Vue templates allow unquoted attributes,
v-bind shorthands, and arbitrary JS expressions that break standard
parsers.  Regex tag-name extraction is cheaper and works on partial or
malformed templates.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = frozenset({"node_modules", "dist", ".git", ".nuxt", ".output"})

# Vue built-in special elements — not local components.
_VUE_BUILT_INS = frozenset({
    "Transition",
    "TransitionGroup",
    "KeepAlive",
    "Teleport",
    "Suspense",
    "Component",
    "Slot",
})

# Extracts the first <template ...> block body.
_TEMPLATE_BLOCK_RE = re.compile(
    r"<template(?:[^>]*)>(.*?)</template>",
    re.DOTALL | re.IGNORECASE,
)

# PascalCase tag names: <MyButton, <RouterView, <BaseCard
_TAG_PASCAL_RE = re.compile(r"<([A-Z][A-Za-z0-9]*)")

# kebab-case tag names with at least one hyphen: <my-button, <base-card
_TAG_KEBAB_RE = re.compile(r"<([a-z][a-z0-9]*(?:-[a-z0-9]+)+)")


def _kebab_to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("-"))


def _stem_to_pascal(stem: str) -> str:
    """Normalise a filename stem to PascalCase regardless of its original casing."""
    if "-" in stem or "_" in stem:
        return "".join(part.capitalize() for part in re.split(r"[-_]", stem))
    # Already PascalCase or camelCase — capitalise the first letter only.
    return stem[0].upper() + stem[1:] if stem else stem


def _nuxt_component_names(rel_posix: str) -> list[str]:
    """Derive Nuxt-style auto-import names for a .vue file path.

    Nuxt 3 prefixes components with their subdirectory path inside
    ``components/``.  ``components/ui/Button.vue`` is used as
    ``<UiButton>``; ``components/base/Alert/index.vue`` as
    ``<BaseAlertIndex>`` (or ``<BaseAlert>`` via Nuxt's index-strip rule).

    Returns a list so both the simple stem name and the prefixed name are
    indexed — covering projects that nest components inside ``components/``
    as well as those that keep them flat.
    """
    parts = rel_posix.split("/")
    stem = Path(parts[-1]).stem
    stem_pascal = _stem_to_pascal(stem)

    # Always include the plain stem name (flat layout or non-Nuxt Vue project).
    names = [stem_pascal]

    # Build prefixed name from directories between ``components/`` and the file.
    try:
        comp_idx = parts.index("components")
    except ValueError:
        return names

    prefix_parts = parts[comp_idx + 1 : -1]  # dirs between components/ and file
    if not prefix_parts:
        return names

    prefix = "".join(_stem_to_pascal(p) for p in prefix_parts)
    prefixed = prefix + stem_pascal
    if prefixed != stem_pascal:
        names.append(prefixed)

    # Nuxt also strips trailing ``Index`` when a component is ``Foo/index.vue``.
    if stem.lower() == "index" and prefix:
        names.append(prefix)

    return names


def _extract_template_body(text: str) -> str | None:
    m = _TEMPLATE_BLOCK_RE.search(text)
    return m.group(1) if m else None


def _extract_component_names(template: str) -> set[str]:
    """Return PascalCase component names referenced in *template*."""
    names: set[str] = set()
    for m in _TAG_PASCAL_RE.finditer(template):
        name = m.group(1)
        if name not in _VUE_BUILT_INS:
            names.add(name)
    for m in _TAG_KEBAB_RE.finditer(template):
        pascal = _kebab_to_pascal(m.group(1))
        if pascal not in _VUE_BUILT_INS:
            names.add(pascal)
    return names


class VueDynamicHints(DynamicHintExtractor):
    """Emit ``dynamic_uses`` edges from Vue templates to their component files."""

    name = "vue"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        vue_files = list(self._rglob(repo_root, "*.vue"))
        if not vue_files:
            return []

        repo_root_resolved = repo_root.resolve()

        # Pass 1: index every .vue file by its normalised component name.
        name_to_files: dict[str, list[str]] = {}
        file_texts: list[tuple[str, str]] = []

        for path in vue_files:
            try:
                rel = path.resolve().relative_to(repo_root_resolved)
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel_str = rel.as_posix()
            file_texts.append((rel_str, text))
            for pascal_name in _nuxt_component_names(rel_str):
                name_to_files.setdefault(pascal_name, []).append(rel_str)

        if not file_texts:
            return []

        # Pass 2: for each file scan its <template> and emit edges.
        edges: list[DynamicEdge] = []
        seen: set[tuple[str, str]] = set()

        for rel_str, text in file_texts:
            template_body = _extract_template_body(text)
            if not template_body:
                continue
            for component_name in _extract_component_names(template_body):
                for target in name_to_files.get(component_name, ()):
                    if target == rel_str:
                        continue
                    key = (rel_str, target)
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append(
                        DynamicEdge(
                            source=rel_str,
                            target=target,
                            edge_type="dynamic_uses",
                            hint_source=f"{self.name}:template",
                        )
                    )

        return edges
