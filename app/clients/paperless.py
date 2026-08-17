from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import httpx


class PaperlessError(Exception):
    pass


@dataclass(slots=True)
class CustomFieldDef:
    id: int
    name: str
    data_type: str
    env_candidates: list[str]


@dataclass(slots=True)
class Document:
    id: int
    title: str
    created_date: date | None
    added: date | None
    modified: datetime | None
    correspondent_name: str
    content: str
    custom_fields: dict[int, Any]
    raw: dict[str, Any]

    def custom_value(self, field_id: int | None) -> Any:
        if field_id is None:
            return None
        return self.custom_fields.get(field_id)


class PaperlessClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        if not base_url or not token:
            raise PaperlessError("Paperless URL and token are required")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Token {token}",
                "Accept": "application/json",
            },
            timeout=timeout,
            follow_redirects=True,
            event_hooks={"request": [self._reapply_auth_on_redirect]},
        )
        self._correspondent_cache: dict[int, str] = {}

    async def _reapply_auth_on_redirect(self, request: httpx.Request) -> None:
        # httpx may drop Authorization on scheme/host changes (http→https).
        request.headers["Authorization"] = f"Token {self._token}"
        request.headers.setdefault("Accept", "application/json")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> dict[str, Any]:
        # /api/ redirects to the OpenAPI schema UI (406 with Accept: application/json).
        response = await self._client.get(
            "/api/custom_fields/",
            params={"page_size": 1},
        )
        self._raise_for_status(response)
        return response.json()

    async def list_custom_fields(self) -> list[CustomFieldDef]:
        response = await self._client.get("/api/custom_fields/", params={"page_size": 100})
        self._raise_for_status(response)
        payload = response.json()
        rows = payload.get("results") or payload
        if not isinstance(rows, list):
            raise PaperlessError("Unexpected custom_fields response")

        fields: list[CustomFieldDef] = []
        for row in rows:
            field_id = int(row["id"])
            name = str(row.get("name") or "")
            data_type = str(row.get("data_type") or "")
            fields.append(
                CustomFieldDef(
                    id=field_id,
                    name=name,
                    data_type=data_type,
                    env_candidates=_env_candidates_for_paperless(
                        field_id, data_type, name
                    ),
                )
            )
        return fields

    async def list_tags(self) -> list[dict[str, Any]]:
        response = await self._client.get("/api/tags/", params={"page_size": 100})
        self._raise_for_status(response)
        payload = response.json()
        return list(payload.get("results") or [])

    async def resolve_tag_id(self, name_or_id: str) -> int | None:
        text = (name_or_id or "").strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        tags = await self.list_tags()
        wanted = text.lower()
        for tag in tags:
            if str(tag.get("name") or "").lower() == wanted:
                return int(tag["id"])
        return None

    async def get_document(self, document_id: int) -> Document:
        response = await self._client.get(
            f"/api/documents/{document_id}/",
            params={"truncate_content": "true"},
        )
        self._raise_for_status(response)
        return await self._parse_document(response.json())

    async def search_documents(
        self,
        *,
        query: str | None = None,
        title_content: str | None = None,
        created_gte: date | None = None,
        created_lte: date | None = None,
        tags_id_in: list[int] | None = None,
        id_in: list[int] | None = None,
        correspondent_name: str | None = None,
        custom_field_query: list | None = None,
        page_size: int = 25,
        max_pages: int = 2,
        truncate_content: bool = True,
    ) -> list[Document]:
        documents: list[Document] = []
        page = 1
        while page <= max_pages:
            params: dict[str, Any] = {
                "page": page,
                "page_size": page_size,
            }
            if truncate_content:
                params["truncate_content"] = "true"
            if query:
                params["query"] = query
            if title_content:
                params["title_content"] = title_content
            if created_gte is not None:
                params["created__date__gte"] = created_gte.isoformat()
            if created_lte is not None:
                params["created__date__lte"] = created_lte.isoformat()
            if tags_id_in:
                params["tags__id__in"] = ",".join(str(i) for i in tags_id_in)
            if id_in:
                params["id__in"] = ",".join(str(i) for i in id_in)
            if correspondent_name:
                params["correspondent__name__icontains"] = correspondent_name
            if custom_field_query is not None:
                params["custom_field_query"] = json.dumps(
                    custom_field_query, separators=(",", ":")
                )
            response = await self._client.get("/api/documents/", params=params)
            self._raise_for_status(response)
            payload = response.json()
            rows = payload.get("results") or []
            for row in rows:
                documents.append(await self._parse_document(row))
            if not payload.get("next") or not rows:
                break
            page += 1
        return documents

    async def get_documents_by_ids(self, ids: list[int]) -> list[Document]:
        unique = list(dict.fromkeys(ids))
        if not unique:
            return []
        found: list[Document] = []
        for offset in range(0, len(unique), 50):
            chunk = unique[offset : offset + 50]
            batch = await self.search_documents(
                id_in=chunk,
                page_size=len(chunk),
                max_pages=1,
                truncate_content=True,
            )
            by_id = {doc.id: doc for doc in batch}
            for doc_id in chunk:
                if doc_id in by_id:
                    found.append(by_id[doc_id])
                    continue
                try:
                    found.append(await self.get_document(doc_id))
                except Exception:
                    continue
        return found

    async def list_documents(
        self,
        *,
        page_size: int = 100,
        truncate_content: bool = True,
        created_gte: date | None = None,
        created_lte: date | None = None,
    ) -> list[Document]:
        documents: list[Document] = []
        page = 1
        while True:
            params: dict[str, Any] = {
                "page": page,
                "page_size": page_size,
            }
            if truncate_content:
                params["truncate_content"] = "true"
            if created_gte is not None:
                params["created__date__gte"] = created_gte.isoformat()
            if created_lte is not None:
                params["created__date__lte"] = created_lte.isoformat()
            response = await self._client.get("/api/documents/", params=params)
            self._raise_for_status(response)
            payload = response.json()
            rows = payload.get("results") or []
            for row in rows:
                documents.append(await self._parse_document(row))
            if not payload.get("next") or not rows:
                break
            page += 1
        return documents

    async def fetch_document_bytes(
        self,
        document_id: int,
        kind: str = "thumb",
    ) -> tuple[bytes, str]:
        """Download thumb or preview bytes. kind: thumb | preview."""
        if kind not in {"thumb", "preview"}:
            raise PaperlessError(f"Unsupported preview kind: {kind}")
        response = await self._client.get(f"/api/documents/{document_id}/{kind}/")
        self._raise_for_status(response)
        content_type = response.headers.get("content-type") or "application/octet-stream"
        return response.content, content_type.split(";")[0].strip()

    async def list_documents_missing_custom_field(
        self,
        field_id: int,
        *,
        page_size: int = 100,
    ) -> list[Document]:
        """Documents where the custom field is missing or empty."""
        fields = await self.list_custom_fields()
        by_id = {f.id: f for f in fields}
        field_name = by_id[field_id].name if field_id in by_id else str(field_id)
        query = ["OR", [[field_name, "isnull", True], [field_name, "exact", ""]]]
        encoded = quote(json.dumps(query, separators=(",", ":")))

        documents: list[Document] = []
        page = 1
        while True:
            response = await self._client.get(
                "/api/documents/",
                params={
                    "page": page,
                    "page_size": page_size,
                    "truncate_content": "true",
                    "custom_field_query": encoded,
                },
            )
            # Some versions want the query unquoted in params (httpx encodes again).
            if response.status_code == 400:
                response = await self._client.get(
                    f"/api/documents/?page={page}&page_size={page_size}"
                    f"&truncate_content=true&custom_field_query={encoded}"
                )
            self._raise_for_status(response)
            payload = response.json()
            rows = payload.get("results") or []
            for row in rows:
                documents.append(await self._parse_document(row))
            if not payload.get("next") or not rows:
                break
            page += 1
        return documents

    async def modify_custom_fields(
        self,
        document_id: int,
        add_fields: dict[int, Any],
    ) -> None:
        """Set custom field values via bulk_edit, with PATCH fallback."""
        response = await self._client.post(
            "/api/documents/bulk_edit/",
            json={
                "documents": [document_id],
                "method": "modify_custom_fields",
                "parameters": {
                    "add_custom_fields": {
                        str(k): v for k, v in add_fields.items()
                    },
                    "remove_custom_fields": [],
                },
            },
        )
        if response.is_success:
            return

        current = await self._client.get(f"/api/documents/{document_id}/")
        self._raise_for_status(current)
        doc = current.json()
        existing = {
            int(item["field"]): item.get("value")
            for item in (doc.get("custom_fields") or [])
            if "field" in item
        }
        existing.update(add_fields)
        patch = await self._client.patch(
            f"/api/documents/{document_id}/",
            json={
                "custom_fields": [
                    {"field": fid, "value": val} for fid, val in existing.items()
                ]
            },
        )
        self._raise_for_status(patch)

    async def _parse_document(self, row: dict[str, Any]) -> Document:
        correspondent_name = ""
        correspondent = row.get("correspondent")
        if isinstance(correspondent, dict):
            correspondent_name = str(correspondent.get("name") or "")
        elif isinstance(correspondent, int):
            correspondent_name = await self._correspondent_name(correspondent)

        custom: dict[int, Any] = {}
        for item in row.get("custom_fields") or []:
            if not isinstance(item, dict):
                continue
            field_id = item.get("field")
            if field_id is None:
                continue
            custom[int(field_id)] = item.get("value")

        return Document(
            id=int(row["id"]),
            title=str(row.get("title") or ""),
            created_date=_parse_date(row.get("created_date") or row.get("created")),
            added=_parse_date(row.get("added")),
            modified=_parse_datetime(row.get("modified")),
            correspondent_name=correspondent_name,
            content=str(row.get("content") or ""),
            custom_fields=custom,
            raw=row,
        )

    async def _correspondent_name(self, correspondent_id: int) -> str:
        if correspondent_id in self._correspondent_cache:
            return self._correspondent_cache[correspondent_id]
        response = await self._client.get(f"/api/correspondents/{correspondent_id}/")
        if not response.is_success:
            return ""
        name = str(response.json().get("name") or "")
        self._correspondent_cache[correspondent_id] = name
        return name

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        location = response.headers.get("Location", "")
        # Final response after redirects may be HTML login — flag clearly.
        content_type = response.headers.get("Content-Type", "")
        detail = response.text[:500]
        extra = ""
        if location:
            extra += f" Location={location}"
        if "text/html" in content_type:
            extra += " (HTML response — check PAPERLESS_URL scheme/host and API token)"
        raise PaperlessError(
            f"Paperless HTTP {response.status_code}:{extra} {detail}".strip()
        )


def _env_candidates_for_paperless(
    field_id: int,
    data_type: str,
    name: str = "",
) -> list[str]:
    name_l = name.lower()
    preferred: list[str] = []
    if any(k in name_l for k in ("ausgaben", "expense", "ausgabe")):
        preferred.append(f"PL_FIELD_EXPENSE_NUMBER={field_id}")
    if any(k in name_l for k in ("rechnungsnummer", "invoice number", "rechnung")):
        preferred.append(f"PL_FIELD_INVOICE_NUMBER={field_id}")
    if data_type == "url" or any(
        k in name_l for k in ("invoice ninja", "invoiceninja", "link")
    ):
        preferred.append(f"PL_FIELD_INVOICE_NINJA_URL={field_id}")
    if data_type in {"monetary", "float", "integer", "number"} or any(
        k in name_l for k in ("betrag", "amount", "summe")
    ):
        preferred.append(f"PL_FIELD_AMOUNT={field_id}")

    fallback = [
        f"PL_FIELD_INVOICE_NUMBER={field_id}",
        f"PL_FIELD_EXPENSE_NUMBER={field_id}",
        f"PL_FIELD_INVOICE_NINJA_URL={field_id}",
        f"PL_FIELD_AMOUNT={field_id}",
    ]
    # Prefer name/type matches, then unique fallbacks
    ordered: list[str] = []
    for line in preferred + fallback:
        if line not in ordered:
            ordered.append(line)
    return ordered


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
