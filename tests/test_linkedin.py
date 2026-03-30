"""Tests for backend.linkedin — URL detection and HTML extraction."""

from backend.linkedin import is_linkedin_url, _extract_from_html


class TestIsLinkedInUrl:
    def test_standard_url(self):
        assert is_linkedin_url("https://www.linkedin.com/in/satyanadella")

    def test_no_www(self):
        assert is_linkedin_url("https://linkedin.com/in/johndoe")

    def test_trailing_slash(self):
        assert is_linkedin_url("https://www.linkedin.com/in/janedoe/")

    def test_http(self):
        assert is_linkedin_url("http://www.linkedin.com/in/janedoe")

    def test_hyphenated_slug(self):
        assert is_linkedin_url("https://www.linkedin.com/in/jane-doe-123")

    def test_not_linkedin(self):
        assert not is_linkedin_url("https://twitter.com/someone")

    def test_plain_text(self):
        assert not is_linkedin_url("Sarah is a data-driven leader")

    def test_linkedin_company_page(self):
        assert not is_linkedin_url("https://www.linkedin.com/company/acme")

    def test_empty(self):
        assert not is_linkedin_url("")

    def test_with_whitespace(self):
        assert is_linkedin_url("  https://www.linkedin.com/in/satyanadella  ")


class TestExtractFromHtml:
    def test_og_tags(self):
        html = """
        <html><head>
            <meta property="og:title" content="Satya Nadella - Chairman and CEO | LinkedIn" />
            <meta property="og:description" content="Chairman and CEO at Microsoft · Redmond, WA · 500+ connections" />
        </head><body></body></html>
        """
        data = _extract_from_html(html)
        assert data["name"] == "Satya Nadella - Chairman and CEO"
        assert "Chairman and CEO at Microsoft" in data["headline"]

    def test_json_ld(self):
        html = """
        <html><head>
            <meta property="og:title" content="Jane Doe | LinkedIn" />
            <meta property="og:description" content="VP Engineering" />
            <script type="application/ld+json">
            {"@type": "Person", "name": "Jane Doe", "description": "Experienced VP of Engineering with a passion for scaling teams"}
            </script>
        </head><body></body></html>
        """
        data = _extract_from_html(html)
        assert data["name"] == "Jane Doe"
        assert "scaling teams" in data["summary"]

    def test_strips_linkedin_suffix(self):
        html = '<html><head><meta property="og:title" content="John Smith | LinkedIn" /></head></html>'
        data = _extract_from_html(html)
        assert data["name"] == "John Smith"

    def test_empty_html(self):
        data = _extract_from_html("<html><head></head><body></body></html>")
        assert data["name"] == ""
        assert data["headline"] == ""
        assert data["summary"] == ""

    def test_malformed_json_ld_ignored(self):
        html = """
        <html><head>
            <meta property="og:title" content="Test User | LinkedIn" />
            <script type="application/ld+json">not valid json</script>
        </head><body></body></html>
        """
        data = _extract_from_html(html)
        assert data["name"] == "Test User"
