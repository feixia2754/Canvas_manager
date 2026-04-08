"""Canvas REST API client."""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

import requests


class CanvasClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _paginate(self, url: str, params: dict | None = None) -> Iterator[dict]:
        import sys
        while url:
            resp = self.session.get(url, params=params)
            if resp.status_code == 401:
                print(
                    "Error: Canvas API token is invalid or expired.\n"
                    "  Get a new token from: Canvas → Account → Settings → New Access Token\n"
                    "  Then update CANVAS_API_TOKEN in your .env file."
                )
                sys.exit(1)
            if resp.status_code == 403:
                print(
                    "Error: Canvas API token does not have permission to access this resource.\n"
                    "  Make sure the token was generated for your own account."
                )
                sys.exit(1)
            resp.raise_for_status()
            yield from resp.json()
            url = _parse_next_link(resp.headers.get("Link", ""))
            params = None

    def get_active_courses(self) -> list[dict]:
        url = f"{self.base_url}/api/v1/courses"
        return list(self._paginate(url, params={"enrollment_state": "active", "per_page": 100}))

    def get_upcoming_assignments(self, course_id: int | str) -> list[dict]:
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments"
        return list(self._paginate(url, params={"bucket": "upcoming", "per_page": 50}))

    def get_all_upcoming_assignments(self) -> list[dict]:
        courses = self.get_active_courses()
        assignments: list[dict] = []
        for course in courses:
            course_name = course.get("course_code") or course.get("name", "Unknown")
            for a in self.get_upcoming_assignments(course["id"]):
                if a.get("due_at"):
                    a["_course_name"] = course_name
                    assignments.append(a)
        return assignments


def _parse_next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) == 2 and segments[1] == 'rel="next"':
            return segments[0].strip("<>")
    return None


def parse_due_date(due_at: str) -> datetime:
    return datetime.fromisoformat(due_at.replace("Z", "+00:00"))
