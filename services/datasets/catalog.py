from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from services.datasets.metrics import (
    DATASET_SLUG_ARRESTS,
    DATASET_SLUG_JAIL_BOOKINGS,
    DATASET_SLUG_POLICE_CALLS,
    DATASET_SLUG_PUBLIC_MEETINGS,
    DATASET_SLUG_WARRANTS,
)


@dataclass(frozen=True)
class DatasetDefinition:
    slug: str
    title: str
    summary: str
    landing_href: str
    records_href: str
    source_kind: str
    metric_kind: str
    methodology: str


DATASET_DEFINITIONS = MappingProxyType(
    {
        DATASET_SLUG_JAIL_BOOKINGS: DatasetDefinition(
            slug=DATASET_SLUG_JAIL_BOOKINGS,
            title="Jail Bookings",
            summary="Current and recent county jail bookings synchronized from detention feeds.",
            landing_href="/jail-bookings",
            records_href="/jail-bookings",
            source_kind="county jail roster",
            metric_kind="booking feed",
            methodology="Counts current and recent bookings published by county detention sources.",
        ),
        DATASET_SLUG_WARRANTS: DatasetDefinition(
            slug=DATASET_SLUG_WARRANTS,
            title="Warrants",
            summary="Active and historical warrant records published by Montana courts and sheriffs.",
            landing_href="/wanted",
            records_href="/wanted",
            source_kind="warrant roster",
            metric_kind="warrant feed",
            methodology="Counts warrant records maintained through the warrant ingestion pipeline.",
        ),
        DATASET_SLUG_ARRESTS: DatasetDefinition(
            slug=DATASET_SLUG_ARRESTS,
            title="Arrests",
            summary="Arrest-linked records built from blotter entries and current jail bookings.",
            landing_href="/arrests",
            records_href="/arrests",
            source_kind="blotter union",
            metric_kind="arrest feed",
            methodology="Counts arrest-keyword blotter records and current or recent jail bookings.",
        ),
        DATASET_SLUG_PUBLIC_MEETINGS: DatasetDefinition(
            slug=DATASET_SLUG_PUBLIC_MEETINGS,
            title="Public Meetings",
            summary="Meeting notices, agendas, and related public meeting records.",
            landing_href="/public-meetings",
            records_href="/public-meetings",
            source_kind="meeting feed",
            metric_kind="meeting feed",
            methodology="Counts current public meeting records and upcoming agendas.",
        ),
        DATASET_SLUG_POLICE_CALLS: DatasetDefinition(
            slug=DATASET_SLUG_POLICE_CALLS,
            title="Police Calls",
            summary="Public police call and incident records that power statewide call-volume trends.",
            landing_href="/datasets/police-calls",
            records_href="/datasets/police-calls/records",
            source_kind="incident feed",
            metric_kind="call log",
            methodology="Counts public incident records keyed by CFS number and county.",
        ),
    }
)


def get_dataset_definition(slug: str) -> DatasetDefinition:
    try:
        return DATASET_DEFINITIONS[slug]
    except KeyError as exc:
        raise KeyError(f"unknown dataset slug: {slug}") from exc
