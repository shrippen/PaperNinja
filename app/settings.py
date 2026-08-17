from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CustomValueSlot = Literal["custom_value1", "custom_value2", "custom_value3", "custom_value4"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    invoice_ninja_url: str = ""
    invoice_ninja_token: str = ""

    paperless_url: str = ""
    paperless_token: str = ""

    data_dir: str = "data"
    session_https: bool = False

    in_expense_field_invoice_number: CustomValueSlot | None = None
    in_expense_field_paperless_url: CustomValueSlot | None = None

    pl_field_invoice_number: int | None = None
    pl_field_expense_number: int | None = None
    pl_field_invoice_ninja_url: int | None = None
    pl_field_amount: int | None = None
    pl_reverse_queue_tag: str = ""

    match_date_window_days: int = Field(default=7, ge=0)
    match_amount_tolerance: float = Field(default=0.02, ge=0)
    match_min_score: int = Field(default=40, ge=0, le=100)
    match_top_n: int = Field(default=5, ge=1, le=20)

    in_expense_url_template: str = "{base}/expenses/{id}/edit"
    pl_document_url_template: str = "{base}/documents/{id}/"
    audit_log_max_bytes: int = Field(default=1_048_576, ge=16_384)

    @field_validator(
        "invoice_ninja_url",
        "paperless_url",
        mode="before",
    )
    @classmethod
    def strip_trailing_slash(cls, value: object) -> object:
        if isinstance(value, str):
            return value.rstrip("/")
        return value

    @field_validator(
        "in_expense_field_invoice_number",
        "in_expense_field_paperless_url",
        mode="before",
    )
    @classmethod
    def empty_slot_to_none(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator(
        "pl_field_invoice_number",
        "pl_field_expense_number",
        "pl_field_invoice_ninja_url",
        "pl_field_amount",
        mode="before",
    )
    @classmethod
    def empty_int_to_none(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @property
    def in_configured(self) -> bool:
        return bool(self.invoice_ninja_url and self.invoice_ninja_token)

    @property
    def pl_configured(self) -> bool:
        return bool(self.paperless_url and self.paperless_token)

    @property
    def mapping_complete(self) -> bool:
        return all(
            [
                self.in_expense_field_invoice_number,
                self.in_expense_field_paperless_url,
                self.pl_field_invoice_number is not None,
                self.pl_field_expense_number is not None,
                self.pl_field_invoice_ninja_url is not None,
            ]
        )

    def expense_url(self, expense_id: str) -> str:
        return self.in_expense_url_template.format(
            base=self.invoice_ninja_url,
            id=expense_id,
        )

    def document_url(self, document_id: int) -> str:
        return self.pl_document_url_template.format(
            base=self.paperless_url,
            id=document_id,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
