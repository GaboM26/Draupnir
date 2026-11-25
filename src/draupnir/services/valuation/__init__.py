"""Valuation models. Each subclasses ValuationModel (base.py); register new ones in MODELS."""

from draupnir.services.valuation.base import PriceLookup, ValuationModel
from draupnir.services.valuation.dcf import DcfModel
from draupnir.services.valuation.pe import PeModel

MODELS: dict[str, ValuationModel] = {model.name: model for model in (DcfModel(), PeModel())}

__all__ = ["MODELS", "PriceLookup", "ValuationModel"]
