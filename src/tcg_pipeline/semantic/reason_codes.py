from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from tcg_pipeline.semantic.types import Confidence


@dataclass(frozen=True, slots=True)
class ReasonCode:
    code: str
    source_profile: str
    field_name: str
    label: str
    description: str
    confidence_default: Confidence
    promotes_status_alone: bool = False
    requires_corroboration: bool = False
    review_item_template: str | None = None
    # Signal-only codes do not write canonical evidence directly. They may still
    # open review items when the observation needs explicit researcher action.
    signal_only: bool = False


@dataclass(frozen=True, slots=True)
class ReasonCodeRegistry:
    by_code: Mapping[str, ReasonCode]
    by_profile_field: Mapping[tuple[str, str], Mapping[str, ReasonCode]]


def _reason(
    code: str,
    field_name: str,
    label: str,
    *,
    description: str,
    confidence_default: Confidence,
    promotes_status_alone: bool = False,
    requires_corroboration: bool = False,
    review_item_template: str | None = None,
    signal_only: bool = False,
    source_profile: str = "news_v1",
) -> ReasonCode:
    return ReasonCode(
        code=code,
        source_profile=source_profile,
        field_name=field_name,
        label=label,
        description=description,
        confidence_default=confidence_default,
        promotes_status_alone=promotes_status_alone,
        requires_corroboration=requires_corroboration,
        review_item_template=review_item_template,
        signal_only=signal_only,
    )


NEWS_STATUS_REASON_CODES: Mapping[str, ReasonCode] = {
    code.code: code
    for code in (
        _reason(
            "news_topped_out",
            "pipeline_status",
            "Topped out",
            description="Article reports the project has topped out.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_framing_complete",
            "pipeline_status",
            "Framing complete",
            description="Article reports framing is complete.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_concrete_pour",
            "pipeline_status",
            "Concrete or foundation poured",
            description="Article reports a concrete pour or poured foundation.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_construction_midpoint",
            "pipeline_status",
            "Construction midpoint",
            description="Article reports the project is substantially through construction.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_vertical_construction",
            "pipeline_status",
            "Vertical construction",
            description="Article reports vertical construction has begun.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_ribbon_cutting",
            "pipeline_status",
            "Ribbon cutting",
            description="Article reports a ribbon-cutting ceremony.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_first_move_ins",
            "pipeline_status",
            "First move-ins",
            description="Article reports first residents or tenants have moved in.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_officially_opened",
            "pipeline_status",
            "Officially opened",
            description="Article reports the project is officially open.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_construction_complete",
            "pipeline_status",
            "Construction complete",
            description="Article reports construction is complete.",
            confidence_default="high",
            promotes_status_alone=True,
        ),
        _reason(
            "news_status_uncorroborated_high_quality_permit_jurisdiction",
            "pipeline_status",
            "Uncorroborated construction news",
            description="Ambiguous construction news in a high-quality permit jurisdiction.",
            confidence_default="medium",
            requires_corroboration=True,
            review_item_template="news_status_uncorroborated",
        ),
        _reason(
            "news_groundbreaking_unverified_low_quality_permit_jurisdiction",
            "pipeline_status",
            "Unverified groundbreaking",
            description="Ambiguous construction news in a low-quality permit jurisdiction.",
            confidence_default="medium",
            promotes_status_alone=True,
        ),
        _reason(
            "news_status_forward_looking_signal_flag_only",
            "pipeline_status",
            "Forward-looking status signal",
            description="Article describes future status activity, not current status.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_leasing_marketing_signal_flag_only",
            "pipeline_status",
            "Leasing marketing signal",
            description="Article reports leasing marketing activity, not verified prices.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_status_delivery_slip",
            "pipeline_status",
            "Delivery slip observed",
            description="Article reports a delayed delivery timeline.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_status_stalled_signal_flag_only",
            "pipeline_status",
            "Stalled signal observed",
            description="Article reports stalled, halted, paused, or on-hold language.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_status_cancellation_review_required",
            "pipeline_status",
            "Cancellation review required",
            description="Article reports cancelled, scrapped, or killed project language.",
            confidence_default="high",
            review_item_template="project_cancellation_review",
            signal_only=True,
        ),
        _reason(
            "news_status_unmappable",
            "pipeline_status",
            "Unmappable status terminology",
            description="Article status language cannot be mapped to the TCG status taxonomy.",
            confidence_default="low",
            signal_only=True,
        ),
    )
}


NEWS_PRODUCT_TYPE_REASON_CODES: Mapping[str, ReasonCode] = {
    code.code: code
    for code in (
        _reason(
            "news_product_type_explicit_apartment",
            "product_type",
            "Apartment product type",
            description="Article explicitly describes an apartment project.",
            confidence_default="high",
        ),
        _reason(
            "news_product_type_explicit_condo",
            "product_type",
            "Condo product type",
            description="Article explicitly describes a condo project.",
            confidence_default="high",
        ),
        _reason(
            "news_product_type_explicit_townhome",
            "product_type",
            "Townhome product type",
            description="Article explicitly describes a townhome project.",
            confidence_default="high",
        ),
        _reason(
            "news_product_type_explicit_single_family",
            "product_type",
            "Single-family product type",
            description="Article explicitly describes a single-family project.",
            confidence_default="high",
        ),
        _reason(
            "news_product_type_explicit_micro_co_living",
            "product_type",
            "Micro/co-living product type",
            description="Article explicitly describes micro-unit or co-living housing.",
            confidence_default="high",
        ),
        _reason(
            "news_product_type_care_based_senior",
            "product_type",
            "Care-based senior signal",
            description="Article describes senior-care product language outside the current enum.",
            confidence_default="medium",
        ),
        _reason(
            "news_product_type_hotel",
            "product_type",
            "Hotel signal",
            description="Article describes hotel or hospitality product language.",
            confidence_default="medium",
        ),
        _reason(
            "news_product_type_student_housing",
            "product_type",
            "Student housing signal",
            description="Article describes purpose-built student housing.",
            confidence_default="medium",
        ),
        _reason(
            "news_product_type_mixed_use",
            "product_type",
            "Mixed-use signal",
            description="Article describes a material mixed-use component.",
            confidence_default="medium",
        ),
        _reason(
            "news_product_type_unmappable",
            "product_type",
            "Unmappable product type",
            description="Article product language does not map safely to a canonical type.",
            confidence_default="low",
            signal_only=True,
        ),
    )
}


LADBS_PRODUCT_TYPE_REASON_CODES: Mapping[str, ReasonCode] = {
    code.code: code
    for code in (
        _reason(
            "ladbs_product_type_apartment",
            "product_type",
            "LADBS apartment product type",
            description="LADBS permit row describes apartment residential use.",
            confidence_default="high",
            source_profile="permit_v1",
        ),
        _reason(
            "ladbs_product_type_condo",
            "product_type",
            "LADBS condo product type",
            description="LADBS permit row describes condominium residential use.",
            confidence_default="high",
            source_profile="permit_v1",
        ),
        _reason(
            "ladbs_product_type_townhome",
            "product_type",
            "LADBS townhome product type",
            description="LADBS permit row describes townhome or townhouse residential use.",
            confidence_default="high",
            source_profile="permit_v1",
        ),
        _reason(
            "ladbs_product_type_single_family",
            "product_type",
            "LADBS single-family product type",
            description="LADBS permit row describes single-family or 1-2 family dwelling use.",
            confidence_default="medium",
            source_profile="permit_v1",
        ),
        _reason(
            "ladbs_product_type_micro_co_living",
            "product_type",
            "LADBS micro/co-living product type",
            description="LADBS permit row describes micro-unit or co-living residential use.",
            confidence_default="high",
            source_profile="permit_v1",
        ),
    )
}


NEWS_MISC_REASON_CODES: Mapping[str, ReasonCode] = {
    code.code: code
    for code in (
        _reason(
            "news_age_restriction_explicit_senior",
            "age_restriction",
            "Senior age restriction",
            description="Article explicitly reports senior or age-restricted housing.",
            confidence_default="high",
        ),
        _reason(
            "news_age_restriction_explicit_student",
            "age_restriction",
            "Student age restriction",
            description="Article explicitly reports student-restricted housing.",
            confidence_default="high",
        ),
        _reason(
            "news_age_restriction_explicit_unrestricted",
            "age_restriction",
            "No age restriction",
            description="Article explicitly reports no age restriction.",
            confidence_default="high",
        ),
        _reason(
            "news_age_restriction_unmappable",
            "age_restriction",
            "Unmappable age-restriction terminology",
            description="Article age-restriction language cannot be mapped safely.",
            confidence_default="low",
            signal_only=True,
        ),
        _reason(
            "news_delivery_date_explicit",
            "date_delivery",
            "Explicit delivery date",
            description="Article states an explicit delivery date.",
            confidence_default="high",
        ),
        _reason(
            "news_delivery_date_projected_season",
            "date_delivery",
            "Projected seasonal delivery date",
            description="Article uses vague seasonal delivery timing.",
            confidence_default="medium",
        ),
        _reason(
            "news_delivery_date_projected_quarter",
            "date_delivery",
            "Projected quarter delivery date",
            description="Article uses quarter-based delivery timing.",
            confidence_default="medium",
        ),
        _reason(
            "news_delivery_date_projected_year_only",
            "date_delivery",
            "Projected year-only delivery date",
            description="Article gives only a delivery year.",
            confidence_default="medium",
        ),
        _reason(
            "news_delivery_date_unmappable",
            "date_delivery",
            "Unmappable delivery timing",
            description="Article delivery-timing language cannot be mapped safely.",
            confidence_default="low",
            signal_only=True,
        ),
        _reason(
            "news_units_total_explicit",
            "total_units",
            "Explicit total units",
            description="Article states total unit count.",
            confidence_default="high",
        ),
        _reason(
            "news_units_affordable_explicit",
            "affordable_units",
            "Explicit affordable units",
            description="Article states affordable unit count.",
            confidence_default="high",
        ),
        _reason(
            "news_units_workforce_explicit",
            "workforce_units",
            "Explicit workforce units",
            description="Article states workforce unit count.",
            confidence_default="high",
        ),
        _reason(
            "news_units_market_rate_explicit",
            "market_rate_units",
            "Explicit market-rate units",
            description="Article states market-rate unit count.",
            confidence_default="high",
        ),
        _reason(
            "news_stories_explicit",
            "stories",
            "Explicit story count",
            description="Article states the building story count.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_retail_sf_explicit",
            "retail_sf",
            "Explicit retail square footage",
            description="Article states retail square footage.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_office_sf_explicit",
            "office_sf",
            "Explicit office square footage",
            description="Article states office square footage.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_hotel_keys_explicit",
            "hotel_keys",
            "Explicit hotel key count",
            description="Article states hotel key count.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_total_sf_explicit",
            "total_sf",
            "Explicit total square footage",
            description="Article states total project square footage.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_affordable_type_lihtc_observed",
            "affordable_type",
            "LIHTC observed",
            description="Article reports LIHTC affordability program language.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_affordable_type_ed1_observed",
            "affordable_type",
            "ED1 observed",
            description="Article reports ED1 streamlining language.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_affordable_type_toc_observed",
            "affordable_type",
            "TOC observed",
            description="Article reports Transit Oriented Communities program language.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_affordable_type_density_bonus_observed",
            "affordable_type",
            "Density bonus observed",
            description="Article reports density bonus program language.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_ceqa_status_draft_eir_released",
            "ceqa_status",
            "Draft EIR released",
            description="Article reports a draft EIR release.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_ceqa_status_final_eir_certified",
            "ceqa_status",
            "Final EIR certified",
            description="Article reports final EIR certification.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_ceqa_status_exemption_observed",
            "ceqa_status",
            "CEQA exemption observed",
            description="Article reports CEQA exemption language.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_appeal_status_filed",
            "appeal_status",
            "Appeal filed",
            description="Article reports an appeal was filed.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_appeal_status_denied",
            "appeal_status",
            "Appeal denied",
            description="Article reports an appeal was denied.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_appeal_status_challenge_observed",
            "appeal_status",
            "Legal challenge observed",
            description="Article reports an EIR or entitlement challenge.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_tenure_unstated_no_default",
            "rent_or_sale",
            "Tenure unknown",
            description="Article does not explicitly state tenure; no for-sale default applied.",
            confidence_default="low",
            signal_only=True,
        ),
        _reason(
            "news_tenure_explicit_rental",
            "rent_or_sale",
            "Explicit rental tenure",
            description="Article explicitly describes rental tenure.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_tenure_explicit_for_sale",
            "rent_or_sale",
            "Explicit for-sale tenure",
            description="Article explicitly describes for-sale tenure.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_tenure_sfr_btr_explicit",
            "rent_or_sale",
            "SFR/BTR tenure",
            description="Article explicitly describes single-family rental or build-to-rent.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_tenure_townhome_rental_explicit",
            "rent_or_sale",
            "Townhome rental tenure",
            description="Article explicitly describes townhome rental tenure.",
            confidence_default="high",
            signal_only=True,
        ),
        _reason(
            "news_tenure_mixed_split_observed",
            "rent_or_sale",
            "Mixed-tenure split",
            description="Article describes separate rental and for-sale components.",
            confidence_default="high",
            review_item_template="multi_tenure_review",
            signal_only=True,
        ),
        _reason(
            "news_tenure_unmappable",
            "rent_or_sale",
            "Unmappable tenure terminology",
            description="Article tenure language cannot be mapped safely.",
            confidence_default="low",
            signal_only=True,
        ),
        _reason(
            "news_address_intersection_synthesized",
            "candidate_address",
            "Intersection address synthesized",
            description="Article describes an intersection that can be geocoded.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_address_landmark_relative_unresolved",
            "candidate_address",
            "Landmark-relative location",
            description="Article describes a landmark-relative location that v1 leaves unresolved.",
            confidence_default="low",
            signal_only=True,
        ),
        _reason(
            "news_address_block_level_synthesized",
            "candidate_address",
            "Block-level address synthesized",
            description="Article describes a block-level location that can be approximated.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_project_name_extracted",
            "candidate_name",
            "Project name extracted",
            description="Article provides a project name after cleanup.",
            confidence_default="medium",
        ),
        _reason(
            "news_project_name_aliases_detected",
            "candidate_name",
            "Project aliases detected",
            description="Article provides previous or alternate project names.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_identifier_extracted",
            "candidate_identifiers",
            "Identifier extracted",
            description="Article contains case, permit, parcel, or source identifiers.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_developer_explicit_role",
            "developer",
            "Developer role explicit",
            description="Article names an entity in the developer role.",
            confidence_default="high",
        ),
        _reason(
            "news_developer_inferred_no_explicit_role",
            "developer",
            "Developer role inferred",
            description="Article names one likely developer without explicit role language.",
            confidence_default="medium",
        ),
        _reason(
            "news_role_landowner",
            "developer_roles",
            "Landowner observed",
            description="Article names a landowner or owner role.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_role_architect",
            "developer_roles",
            "Architect observed",
            description="Article names an architect role.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_role_gc",
            "developer_roles",
            "General contractor observed",
            description="Article names a general contractor or construction manager role.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_role_operator",
            "developer_roles",
            "Operator observed",
            description="Article names an operator or property manager role.",
            confidence_default="medium",
            signal_only=True,
        ),
        _reason(
            "news_role_equity_partner",
            "developer_roles",
            "Equity partner observed",
            description="Article names an investor or equity partner role.",
            confidence_default="medium",
            signal_only=True,
        ),
    )
}


def _build_by_code(groups: Iterable[Mapping[str, ReasonCode]]) -> Mapping[str, ReasonCode]:
    by_code: dict[str, ReasonCode] = {}
    duplicates: set[str] = set()
    for group in groups:
        for code, reason in group.items():
            if code in by_code:
                duplicates.add(code)
            by_code[code] = reason
    if duplicates:
        raise ValueError(f"Duplicate semantic reason codes: {sorted(duplicates)}")
    return dict(by_code)


def _group_by_profile_field(
    reason_codes: Iterable[ReasonCode],
) -> Mapping[tuple[str, str], Mapping[str, ReasonCode]]:
    grouped: dict[tuple[str, str], dict[str, ReasonCode]] = defaultdict(dict)
    for reason in reason_codes:
        grouped[(reason.source_profile, reason.field_name)][reason.code] = reason
    return {key: dict(value) for key, value in grouped.items()}


REASON_CODE_GROUPS: Mapping[str, Mapping[str, ReasonCode]] = {
    "news_v1.pipeline_status": NEWS_STATUS_REASON_CODES,
    "news_v1.product_type": NEWS_PRODUCT_TYPE_REASON_CODES,
    "news_v1.misc": NEWS_MISC_REASON_CODES,
    "permit_v1.product_type": LADBS_PRODUCT_TYPE_REASON_CODES,
}

REASON_CODES_BY_CODE: Mapping[str, ReasonCode] = _build_by_code(REASON_CODE_GROUPS.values())

REASON_CODES_BY_PROFILE_FIELD: Mapping[tuple[str, str], Mapping[str, ReasonCode]] = (
    _group_by_profile_field(REASON_CODES_BY_CODE.values())
)


def build_reason_code_registry(
    extra_reason_codes: Iterable[ReasonCode] = (),
) -> ReasonCodeRegistry:
    by_code: dict[str, ReasonCode] = dict(REASON_CODES_BY_CODE)
    duplicates: set[str] = set()
    for reason in extra_reason_codes:
        if not reason.code:
            raise ValueError("Reason-code extension code is required")
        if not reason.source_profile:
            raise ValueError(f"Reason-code extension {reason.code} is missing source_profile")
        if not reason.field_name:
            raise ValueError(f"Reason-code extension {reason.code} is missing field_name")
        if reason.code in by_code:
            duplicates.add(reason.code)
        by_code[reason.code] = reason
    if duplicates:
        raise ValueError(f"Duplicate semantic reason codes: {sorted(duplicates)}")
    return ReasonCodeRegistry(
        by_code=dict(by_code),
        by_profile_field=_group_by_profile_field(by_code.values()),
    )


def reason_code_for(code: str) -> ReasonCode:
    try:
        return REASON_CODES_BY_CODE[code]
    except KeyError as exc:
        raise KeyError(f"Unknown semantic reason code: {code}") from exc


def validate_reason_code_registry(registry: ReasonCodeRegistry | None = None) -> None:
    if registry is not None:
        regrouped = _group_by_profile_field(registry.by_code.values())
        if regrouped != registry.by_profile_field:
            raise ValueError("Semantic reason-code profile/field index is stale")
        return
    seen: set[str] = set()
    duplicates: set[str] = set()
    for group in REASON_CODE_GROUPS.values():
        for code in group:
            if code in seen:
                duplicates.add(code)
            seen.add(code)
    if duplicates:
        raise ValueError(f"Duplicate semantic reason codes: {sorted(duplicates)}")
    missing = seen - set(REASON_CODES_BY_CODE)
    if missing:
        raise ValueError(f"Grouped reason codes missing from registry: {sorted(missing)}")
    dangling = set(REASON_CODES_BY_CODE) - seen
    if dangling:
        raise ValueError(f"Registry reason codes missing from groups: {sorted(dangling)}")
