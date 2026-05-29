"""Unit tests for VueDynamicHints template component extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.dynamic_hints.vue import (
    VueDynamicHints,
    _extract_component_names,
    _extract_template_body,
    _kebab_to_pascal,
    _nuxt_component_names,
    _stem_to_pascal,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

class TestKebabToPascal:
    def test_simple(self) -> None:
        assert _kebab_to_pascal("my-button") == "MyButton"

    def test_three_parts(self) -> None:
        assert _kebab_to_pascal("base-input-field") == "BaseInputField"

    def test_single_word(self) -> None:
        assert _kebab_to_pascal("button") == "Button"


class TestStemToPascal:
    def test_already_pascal(self) -> None:
        assert _stem_to_pascal("MyButton") == "MyButton"

    def test_kebab_stem(self) -> None:
        assert _stem_to_pascal("my-button") == "MyButton"

    def test_snake_stem(self) -> None:
        assert _stem_to_pascal("my_button") == "MyButton"

    def test_lowercase_stem(self) -> None:
        assert _stem_to_pascal("button") == "Button"


class TestExtractTemplateBody:
    def test_extracts_body(self) -> None:
        src = "<template><div>Hello</div></template>"
        assert _extract_template_body(src) == "<div>Hello</div>"

    def test_multiline(self) -> None:
        src = "<template>\n  <MyButton />\n</template>"
        body = _extract_template_body(src)
        assert body is not None
        assert "MyButton" in body

    def test_no_template_returns_none(self) -> None:
        src = "<script setup>\nimport x from 'y'\n</script>"
        assert _extract_template_body(src) is None

    def test_template_with_lang_attr(self) -> None:
        src = '<template lang="html"><span /></template>'
        assert _extract_template_body(src) == "<span />"


class TestExtractComponentNames:
    def test_pascal_case(self) -> None:
        names = _extract_component_names("<MyButton /><BaseCard />")
        assert "MyButton" in names
        assert "BaseCard" in names

    def test_kebab_case_converted(self) -> None:
        names = _extract_component_names("<my-button /><base-card />")
        assert "MyButton" in names
        assert "BaseCard" in names

    def test_builtin_excluded(self) -> None:
        names = _extract_component_names(
            "<Transition><div /></Transition>"
            "<KeepAlive><span /></KeepAlive>"
            "<Teleport to='body' />"
            "<Suspense />"
            "<Component :is='x' />"
            "<Slot />"
        )
        assert "Transition" not in names
        assert "KeepAlive" not in names
        assert "Teleport" not in names
        assert "Suspense" not in names
        assert "Component" not in names
        assert "Slot" not in names

    def test_html_native_tags_excluded(self) -> None:
        names = _extract_component_names("<div><span><p><input></p></span></div>")
        # native lowercase HTML tags — no PascalCase and no hyphens
        assert not names

    def test_mixed(self) -> None:
        template = """
        <template>
          <div class="wrapper">
            <MyHeader />
            <router-view />
            <BaseCard v-for="item in items" :key="item.id">
              <span>{{ item.name }}</span>
            </BaseCard>
          </div>
        </template>
        """
        names = _extract_component_names(template)
        assert "MyHeader" in names
        assert "RouterView" in names  # kebab → pascal
        assert "BaseCard" in names


# ---------------------------------------------------------------------------
# Full extractor (filesystem-based)
# ---------------------------------------------------------------------------

class TestNuxtComponentNames:
    def test_flat_component_returns_stem(self) -> None:
        assert _nuxt_component_names("components/Button.vue") == ["Button"]

    def test_nested_one_level_returns_prefixed_and_stem(self) -> None:
        names = _nuxt_component_names("components/ui/Button.vue")
        assert "UiButton" in names
        assert "Button" in names

    def test_nested_two_levels(self) -> None:
        names = _nuxt_component_names("components/ui/base/Alert.vue")
        assert "UiBaseAlert" in names
        assert "Alert" in names

    def test_index_file_strips_index_suffix(self) -> None:
        # components/ui/index.vue → "UiIndex" AND "Ui" (Nuxt strips Index)
        names = _nuxt_component_names("components/ui/index.vue")
        assert "Ui" in names

    def test_no_components_dir_returns_stem_only(self) -> None:
        names = _nuxt_component_names("src/views/Modal.vue")
        assert names == ["Modal"]

    def test_kebab_dir_name_converted(self) -> None:
        names = _nuxt_component_names("components/base-ui/Card.vue")
        assert "BaseUiCard" in names


class TestVueDynamicHints:
    def test_no_vue_files_returns_empty(self, tmp_path: Path) -> None:
        edges = VueDynamicHints().extract(tmp_path)
        assert edges == []

    def test_single_file_no_template_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "App.vue").write_text(
            "<script setup>\nimport x from 'y'\n</script>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        assert edges == []

    def test_emits_edge_for_auto_imported_component(self, tmp_path: Path) -> None:
        (tmp_path / "MyButton.vue").write_text(
            "<template><button>click</button></template>", encoding="utf-8"
        )
        (tmp_path / "App.vue").write_text(
            "<template><MyButton /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        assert len(edges) == 1
        edge = edges[0]
        assert edge.source == "App.vue"
        assert edge.target == "MyButton.vue"
        assert edge.edge_type == "dynamic_uses"
        assert edge.hint_source == "vue:template"

    def test_kebab_usage_resolves_to_pascal_file(self, tmp_path: Path) -> None:
        (tmp_path / "BaseCard.vue").write_text(
            "<template><div /></template>", encoding="utf-8"
        )
        (tmp_path / "App.vue").write_text(
            "<template><base-card /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        sources = {(e.source, e.target) for e in edges}
        assert ("App.vue", "BaseCard.vue") in sources

    def test_no_self_edge(self, tmp_path: Path) -> None:
        """A component referencing itself should not produce a self-loop."""
        (tmp_path / "Tree.vue").write_text(
            "<template><Tree v-if='node.children' /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        assert all(e.source != e.target for e in edges)

    def test_no_duplicate_edges(self, tmp_path: Path) -> None:
        """Multiple usages of the same component produce only one edge."""
        (tmp_path / "Btn.vue").write_text(
            "<template><button /></template>", encoding="utf-8"
        )
        (tmp_path / "App.vue").write_text(
            "<template><Btn /><Btn /><Btn /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        pairs = [(e.source, e.target) for e in edges]
        assert len(pairs) == len(set(pairs))

    def test_unknown_component_produces_no_edge(self, tmp_path: Path) -> None:
        """Library components (e.g. Vuetify's VBtn) are silently ignored."""
        (tmp_path / "App.vue").write_text(
            "<template><VBtn>click</VBtn></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        assert edges == []

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules" / "some-lib"
        nm.mkdir(parents=True)
        (nm / "LibComp.vue").write_text(
            "<template><div /></template>", encoding="utf-8"
        )
        (tmp_path / "App.vue").write_text(
            "<template><LibComp /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        # LibComp.vue is inside node_modules — not indexed, no edge
        assert edges == []

    def test_subdirectory_components(self, tmp_path: Path) -> None:
        comp_dir = tmp_path / "components"
        comp_dir.mkdir()
        (comp_dir / "UserCard.vue").write_text(
            "<template><div /></template>", encoding="utf-8"
        )
        (tmp_path / "App.vue").write_text(
            "<template><UserCard /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        sources = {(e.source, e.target) for e in edges}
        assert ("App.vue", "components/UserCard.vue") in sources

    def test_nuxt_path_prefix_matching(self, tmp_path: Path) -> None:
        """components/ui/Button.vue used as <UiButton> in Nuxt 3."""
        ui_dir = tmp_path / "components" / "ui"
        ui_dir.mkdir(parents=True)
        (ui_dir / "Button.vue").write_text(
            "<template><button /></template>", encoding="utf-8"
        )
        (tmp_path / "App.vue").write_text(
            "<template><UiButton /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        sources = {(e.source, e.target) for e in edges}
        assert ("App.vue", "components/ui/Button.vue") in sources

    def test_nuxt_index_component_name(self, tmp_path: Path) -> None:
        """components/base/index.vue used as <Base> in Nuxt 3."""
        base_dir = tmp_path / "components" / "base"
        base_dir.mkdir(parents=True)
        (base_dir / "index.vue").write_text(
            "<template><div /></template>", encoding="utf-8"
        )
        (tmp_path / "App.vue").write_text(
            "<template><Base /></template>", encoding="utf-8"
        )
        edges = VueDynamicHints().extract(tmp_path)
        sources = {(e.source, e.target) for e in edges}
        assert ("App.vue", "components/base/index.vue") in sources
