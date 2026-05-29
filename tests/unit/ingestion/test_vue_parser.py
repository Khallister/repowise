"""Unit tests for Vue SFC two-phase parsing (Option A MVP)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file


def _fi(rel: str = "src/App.vue") -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=f"/repo/{rel}",
        language="vue",
        size_bytes=0,
        git_hash="",
        last_modified=datetime(2024, 1, 1),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse(src: str, rel: str = "src/App.vue"):
    fi = _fi(rel)
    return parse_file(fi, src.encode("utf-8"))


class TestVueScriptSetupTs:
    def test_imports_extracted(self) -> None:
        src = """\
<template><div>Hello</div></template>
<script setup lang="ts">
import { ref } from 'vue'
import MyButton from './components/MyButton.vue'
import { useUser } from '@/composables/useUser'
const count = ref(0)
</script>
"""
        result = _parse(src)
        modules = {imp.module_path for imp in result.imports}
        assert "vue" in modules
        assert "./components/MyButton.vue" in modules
        assert "@/composables/useUser" in modules

    def test_language_tag_preserved(self) -> None:
        src = """\
<script setup lang="ts">
import { ref } from 'vue'
</script>
"""
        result = _parse(src)
        assert result.file_info.language == "vue"
        assert result.file_info.path == "src/App.vue"

    def test_symbols_extracted(self) -> None:
        src = """\
<script setup lang="ts">
import { computed } from 'vue'
const double = computed(() => 2)
function greet(name: string): string { return name }
</script>
"""
        result = _parse(src)
        names = {s.name for s in result.symbols}
        assert "greet" in names


class TestVuePlainScript:
    def test_plain_script_lang_ts(self) -> None:
        src = """\
<script lang="ts">
import { defineComponent } from 'vue'
export default defineComponent({ name: 'App' })
</script>
"""
        result = _parse(src)
        modules = {imp.module_path for imp in result.imports}
        assert "vue" in modules

    def test_plain_script_no_lang(self) -> None:
        """<script> without lang="ts" → parsed as JavaScript, still extracts imports."""
        src = """\
<script>
import axios from 'axios'
export default { name: 'App' }
</script>
"""
        result = _parse(src)
        modules = {imp.module_path for imp in result.imports}
        assert "axios" in modules


class TestVueScriptPriority:
    def test_script_setup_preferred_over_plain_script(self) -> None:
        """When both <script> and <script setup> exist, setup takes priority."""
        src = """\
<script lang="ts">
import { defineComponent } from 'vue'
export default defineComponent({})
</script>
<script setup lang="ts">
import { ref } from 'vue'
import OnlyInSetup from './OnlyInSetup.vue'
</script>
"""
        result = _parse(src)
        modules = {imp.module_path for imp in result.imports}
        assert "./OnlyInSetup.vue" in modules
        # defineComponent import is from the non-setup block — should NOT appear
        # because setup block is preferred and parsed exclusively
        assert "defineComponent" not in {
            name for imp in result.imports for name in imp.imported_names
        }


class TestVueNoScript:
    def test_no_script_block_returns_empty(self) -> None:
        src = """\
<template>
  <div>Template only</div>
</template>
<style scoped>
div { color: red; }
</style>
"""
        result = _parse(src)
        assert result.imports == []
        assert result.symbols == []
        assert result.parse_errors == []
        assert result.file_info.language == "vue"

    def test_empty_script_block_returns_empty(self) -> None:
        src = "<script setup lang=\"ts\">\n</script>\n"
        result = _parse(src)
        assert result.imports == []

    def test_completely_empty_file(self) -> None:
        result = _parse("")
        assert result.imports == []
        assert result.symbols == []


class TestVueLineOffsets:
    def test_symbol_line_corrected_by_template_offset(self) -> None:
        """Symbol line numbers must account for lines before the <script> block."""
        src = """\
<template><div>Hello</div></template>
<script setup lang="ts">
import { ref } from 'vue'
function greet(name: string): string { return name }
</script>
"""
        # file line layout (1-indexed):
        # 1: <template>...</template>
        # 2: <script setup lang="ts">
        # 3: import { ref } from 'vue'
        # 4: function greet(...)
        result = _parse(src)
        greet = next(s for s in result.symbols if s.name == "greet")
        assert greet.start_line == 4

    def test_symbol_line_no_offset_when_script_first(self) -> None:
        """When <script> is the first block, offset is 0 and lines are unchanged."""
        src = """\
<script setup lang="ts">
import { ref } from 'vue'
function greet(name: string): string { return name }
</script>
<template><div>Hello</div></template>
"""
        # file line layout (1-indexed):
        # 1: <script setup lang="ts">
        # 2: import { ref } from 'vue'
        # 3: function greet(...)
        result = _parse(src)
        greet = next(s for s in result.symbols if s.name == "greet")
        assert greet.start_line == 3

    def test_symbol_end_line_also_corrected(self) -> None:
        src = """\
<template><div /></template>
<style scoped>div { color: red; }</style>
<script setup lang="ts">
function multi(
  a: number,
  b: number,
): number { return a + b }
</script>
"""
        # line 1: <template>
        # line 2: <style>
        # line 3: <script setup lang="ts">
        # line 4: function multi(
        # ...
        # line 7: ): number { return a + b }
        result = _parse(src)
        multi = next(s for s in result.symbols if s.name == "multi")
        assert multi.start_line == 4
        assert multi.end_line == 7
