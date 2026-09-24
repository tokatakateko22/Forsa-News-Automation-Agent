"""
tests/unit/test_spelling.py
───────────────────────────
Tests for brand and entity spelling normalization.
Guarantees that machine transliterations like "SooLa" are converted to "Souhoola"
and competitor names remain accurately spelled before email dispatch.
"""
from app.services.spelling import normalize_brand_spellings
from app.models.event import EventSummary, NewsEvent
from app.graph.nodes.format_email import _event_plain_block, _event_html_block


class TestBrandSpellingNormalization:
    def test_user_reported_soola_to_souhoola(self):
        text = (
            "SooLa plans to sell financing portfolios valued at $5.8 million during "
            "the fourth quarter of 2026. This strategic move aims to optimize liquidity."
        )
        cleaned = normalize_brand_spellings(text)
        assert "Souhoola" in cleaned
        assert "SooLa" not in cleaned
        assert cleaned.startswith("Souhoola plans to sell")

    def test_competitor_hint_targeted_replacement(self):
        text = "Sohoolah announced new credit limits for consumers."
        cleaned = normalize_brand_spellings(text, competitor_hint="Souhoola")
        assert cleaned.startswith("Souhoola announced")

    def test_valu_normalization(self):
        text = "Valu signed an agreement while Valiu launched zero interest installments."
        cleaned = normalize_brand_spellings(text)
        assert "valU signed" in cleaned
        assert "valU launched" in cleaned

    def test_mnt_halan_normalization(self):
        text = "MNT Halan announced expansion into regional markets."
        cleaned = normalize_brand_spellings(text)
        assert "MNT-Halan announced" in cleaned

    def test_btech_normalization(self):
        text = "Btech finance and B-Tech expanded retail branches."
        cleaned = normalize_brand_spellings(text)
        assert "B.TECH finance" in cleaned
        assert "B.TECH expanded" in cleaned

    def test_fawry_and_ecosystem_normalization(self):
        text = "Fawri partnered with Instapay and integrated i-score reporting."
        cleaned = normalize_brand_spellings(text)
        assert "Fawry partnered" in cleaned
        assert "InstaPay" in cleaned
        assert "I-Score" in cleaned

    def test_email_plain_and_html_blocks_clean_brand_spelling(self):
        summary = EventSummary(
            event_id="e100",
            summary_text="SooLa plans to sell financing portfolios valued at $5.8 million.",
            category="Competitor",
            subcategory="Market Deals & Expansion",
            canonical_title="SooLa portfolio sale",
            canonical_url="https://example.com/soola",
            source_name="Daily News Egypt",
            competitor_name="Souhoola",
        )
        event = NewsEvent(
            event_id="e100",
            canonical_title="SooLa portfolio sale",
            canonical_url="https://example.com/soola",
            canonical_source_name="Daily News Egypt",
            canonical_source_tier=2,
            category="Competitor",
            articles=[],
            summary=summary,
        )

        plain = _event_plain_block(event)
        assert "Souhoola" in plain
        assert "SooLa" not in plain

        html = _event_html_block(event)
        assert "Souhoola" in html
        assert "SooLa" not in html
