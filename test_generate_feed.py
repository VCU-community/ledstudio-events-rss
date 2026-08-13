import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

import generate_feed as feed


FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "events_gviz.txt"


class FeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_text = FIXTURE.read_text(encoding="utf-8")
        cls.rows = feed.rows_from_gviz(cls.source_text)
        cls.events = feed.parse_events(cls.rows)

    def test_fixture_parses_and_deduplicates_identical_rows(self):
        self.assertEqual(len(self.rows), 6)
        self.assertEqual(len(self.events), 5)

    def test_selection_releases_registered_events_and_delays_missing_registration(self):
        selection = feed.select_events(self.events, date(2026, 8, 13))
        self.assertEqual(
            [event.title for event in selection.published],
            ["No End Time Event", "Accessible & Engaging"],
        )
        self.assertEqual([event.title for event in selection.delayed], ["Waiting for Registration"])
        self.assertEqual([event.title for event in selection.future], ["Later Event"])
        self.assertEqual([event.title for event in selection.past], ["Past Event"])

    def test_description_has_requested_fields_and_omits_tags_recording_and_empty_resources(self):
        event = next(item for item in self.events if item.title == "No End Time Event")
        description = feed.event_description_text(event)
        self.assertIn(" | Date:", description)
        self.assertIn(" | Time: 10:00 a.m.", description)
        self.assertIn(" | Location:", description)
        self.assertIn(" | Event details: https://", description)
        self.assertNotIn("Resources:", description)
        self.assertNotIn("Register:", description)
        self.assertNotIn("example.edu/register", description)
        self.assertNotIn("TAGS", description)
        self.assertNotIn("recording", description.lower())
        self.assertNotIn("<", description)

    def test_description_omits_resources_even_when_present(self):
        event = next(item for item in self.events if item.title == "Accessible & Engaging")
        description = feed.event_description_text(event)
        self.assertNotIn("Resources:", description)
        self.assertNotIn("Faculty guide", description)
        self.assertNotIn("learning-resources/guide", description)
        self.assertIn("Accessible & Engaging", event.title)

    def test_rss_is_well_formed_and_has_stable_required_fields(self):
        selection = feed.select_events(self.events, date(2026, 8, 13))
        xml_bytes = feed.build_rss(selection.published, datetime(2026, 8, 13, 12, tzinfo=timezone.utc))
        root = ET.fromstring(xml_bytes)
        items = root.findall("./channel/item")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].findtext("guid"), "urn:vcu:ledstudio:event:event-005")
        self.assertEqual(items[0].find("guid").attrib["isPermaLink"], "false")
        self.assertEqual(items[0].findtext("pubDate"), "Tue, 11 Aug 2026 13:00:00 GMT")
        self.assertEqual(items[1].findtext("guid"), "urn:vcu:ledstudio:event:event-001")
        self.assertEqual(items[1].findtext("pubDate"), "Thu, 13 Aug 2026 13:00:00 GMT")
        self.assertEqual(root.findtext("./channel/ttl"), "360")
        self.assertIsNotNone(root.find(f"./channel/{{{feed.ATOM_NS}}}link"))

    def test_empty_eligible_feed_is_valid(self):
        selection = feed.select_events(self.events, date(2026, 7, 1))
        xml_bytes = feed.build_rss(selection.published, datetime.now(timezone.utc))
        root = ET.fromstring(xml_bytes)
        self.assertEqual(root.findall("./channel/item"), [])

    def test_resource_columns_do_not_affect_event(self):
        event = next(item for item in self.events if item.title == "Accessible & Engaging")
        self.assertFalse(hasattr(event, "resources"))

    def test_missing_required_column_fails(self):
        payload = feed.unwrap_gviz(self.source_text)
        payload["table"]["cols"] = [
            column for column in payload["table"]["cols"] if column.get("label") != feed.TITLE_COLUMN
        ]
        broken = "google.visualization.Query.setResponse(" + json.dumps(payload) + ");"
        with self.assertRaisesRegex(feed.FeedError, "Required sheet columns are missing"):
            feed.rows_from_gviz(broken)

    def test_conflicting_duplicate_guid_fails(self):
        rows = list(self.rows)
        conflict = dict(rows[0])
        conflict[feed.TITLE_COLUMN] = "A different title"
        rows.append(conflict)
        with self.assertRaisesRegex(feed.FeedError, "Conflicting events use the same GUID"):
            feed.parse_events(rows)

    def test_generate_writes_feed_and_status_page(self):
        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name) / "public"
            selection = feed.generate(
                output_dir=output,
                today=date(2026, 8, 13),
                source_text=self.source_text,
            )
            self.assertEqual(len(selection.published), 2)
            self.assertTrue((output / "rss.xml").is_file())
            status = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("LEDstudio Events RSS Preview", status)
            self.assertIn("No End Time Event", status)
            self.assertIn("An end time is not available.", status)
            self.assertNotIn("Register: https://", status)
            self.assertIn("Event details: https://", status)
            self.assertIn(" | Date:", status)
            self.assertIn('class="post-preview"', status)
            self.assertIn("Delayed: registration needed", status)


if __name__ == "__main__":
    unittest.main()
