#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for HotSectorClassifier — 3-category hot sector classification."""

from __future__ import annotations

import pytest

from signal_analysis.hot_sectors import HotSectorClassifier


class TestHotSectorClassifier:
    """单元测试：热点板块三分类器（行业/主题/地域）。"""

    def setup_method(self) -> None:
        self.classifier = HotSectorClassifier()

    # ── 行业分类 ──

    def test_semiconductor_is_industry(self) -> None:
        """半导体 → industry"""
        result = self.classifier.classify(["半导体"])
        assert "半导体" in result["industry"]
        assert "半导体" not in result["theme"]
        assert "半导体" not in result["region"]

    def test_bank_is_industry(self) -> None:
        """银行 → industry"""
        result = self.classifier.classify(["银行"])
        assert "银行" in result["industry"]

    def test_broker_is_industry(self) -> None:
        """券商 → industry"""
        result = self.classifier.classify(["券商"])
        assert "券商" in result["industry"]

    def test_pharmaceutical_is_industry(self) -> None:
        """医药 → industry"""
        result = self.classifier.classify(["医药"])
        assert "医药" in result["industry"]

    def test_new_energy_is_industry(self) -> None:
        """新能源 → industry（既是行业关键词也出现在主题关键词中，行业优先）"""
        result = self.classifier.classify(["新能源"])
        assert "新能源" in result["industry"]
        assert "新能源" not in result["theme"]

    def test_industry_list_has_40plus_items(self) -> None:
        """INDUSTRY_NAMES 至少有 40 个条目"""
        assert len(HotSectorClassifier.INDUSTRY_NAMES) >= 40

    # ── 主题分类 ──

    def test_ai_is_theme(self) -> None:
        """AI → theme"""
        result = self.classifier.classify(["AI"])
        assert "AI" in result["theme"]
        assert "AI" not in result["industry"]
        assert "AI" not in result["region"]

    def test_low_altitude_economy_is_theme(self) -> None:
        """低空经济 → theme"""
        result = self.classifier.classify(["低空经济"])
        assert "低空经济" in result["theme"]

    def test_robot_is_theme(self) -> None:
        """机器人 → theme"""
        result = self.classifier.classify(["机器人"])
        assert "机器人" in result["theme"]

    def test_suanli_is_theme(self) -> None:
        """算力 → theme"""
        result = self.classifier.classify(["算力"])
        assert "算力" in result["theme"]

    def test_xinchuang_is_theme(self) -> None:
        """信创 → theme"""
        result = self.classifier.classify(["信创"])
        assert "信创" in result["theme"]

    # ── 地域分类 ──

    def test_hainan_free_trade_is_region(self) -> None:
        """海南自贸 → region"""
        result = self.classifier.classify(["海南自贸"])
        assert "海南自贸" in result["region"]
        assert "海南自贸" not in result["industry"]
        assert "海南自贸" not in result["theme"]

    def test_yuegangao_is_region(self) -> None:
        """粤港澳 → region"""
        result = self.classifier.classify(["粤港澳"])
        assert "粤港澳" in result["region"]

    def test_chang_san_jiao_is_region(self) -> None:
        """长三角 → region"""
        result = self.classifier.classify(["长三角"])
        assert "长三角" in result["region"]

    # ── 省份名无催化剂 → 跳过 ──

    def test_anhui_no_catalyst_skipped(self) -> None:
        """安徽(无催化剂) → 被跳过"""
        result = self.classifier.classify(["安徽"])
        assert "安徽" not in result["industry"]
        assert "安徽" not in result["theme"]
        assert "安徽" not in result["region"]

    def test_beijing_province_filtered(self) -> None:
        """北京(省份) → 被跳过"""
        result = self.classifier.classify(["北京"])
        assert "北京" not in result["industry"]
        assert "北京" not in result["theme"]
        assert "北京" not in result["region"]

    def test_guangdong_province_skipped(self) -> None:
        """广东(省份) → 被跳过"""
        result = self.classifier.classify(["广东"])
        assert "广东" not in sum(result.values(), [])

    def test_shanghai_province_skipped(self) -> None:
        """上海(直辖市) → 被跳过"""
        result = self.classifier.classify(["上海"])
        assert "上海" not in sum(result.values(), [])

    # ── 混合输入 ──

    def test_mixed_input(self) -> None:
        """混合输入：行业 + 主题 + 地域 + 省份"""
        result = self.classifier.classify([
            "半导体", "AI", "海南自贸", "安徽", "北京",
            "创新药", "低空经济", "粤港澳",
        ])
        assert "半导体" in result["industry"]
        assert "创新药" in result["industry"]
        assert "AI" in result["theme"]
        assert "低空经济" in result["theme"]
        assert "海南自贸" in result["region"]
        assert "粤港澳" in result["region"]
        # 省份无催化剂 — 跳过
        all_values = sum(result.values(), [])
        assert "安徽" not in all_values
        assert "北京" not in all_values

    # ── 边界条件 ──

    def test_empty_input(self) -> None:
        """空输入 → 三个空列表"""
        result = self.classifier.classify([])
        assert result == {"industry": [], "theme": [], "region": []}

    def test_unknown_sector_skipped(self) -> None:
        """未知板块（无子串匹配）→ 跳过"""
        result = self.classifier.classify(["未知板块"])
        assert result["industry"] == []
        assert result["theme"] == []
        assert result["region"] == []

    def test_unknown_with_industry_substring_mapped(self) -> None:
        """"半导体设备"含子串"半导体" → 映射到 industry"""
        result = self.classifier.classify(["半导体设备"])
        assert "半导体" in result["industry"]

    def test_deduplicated_output(self) -> None:
        """去重：相同输入不重复添加"""
        result = self.classifier.classify(["半导体", "半导体", "AI", "AI"])
        assert len(result["industry"]) == 1
        assert len(result["theme"]) == 1
        assert len(result["region"]) == 0

    def test_whitespace_handling(self) -> None:
        """空白字符被正确处理"""
        result = self.classifier.classify([" 半导体 ", "", "  "])
        assert "半导体" in result["industry"]
        assert len(result["industry"]) == 1

    # ── 分类器三桶完整性 ──

    def test_result_keys(self) -> None:
        """返回字典始终包含 industry/theme/region 三个键"""
        result = self.classifier.classify(["半导体", "AI"])
        assert set(result.keys()) == {"industry", "theme", "region"}
