import dataclasses

import pytest
from hunch.model import Entity, HomeModel


def test_home_model_is_frozen(home: HomeModel):
    with pytest.raises(dataclasses.FrozenInstanceError):
        home.floors = ()  # type: ignore[misc]


def test_area_lookup(home: HomeModel):
    assert home.area_by_id("living").name == "Living room"
    assert home.area_by_id("nope") is None


def test_areas_for_floor(home: HomeModel):
    assert home.areas_for_floor("downstairs") == ("kitchen", "living", "hallway")
    assert home.areas_for_floor("attic") == ()


def test_domains_are_sorted_and_exclude_scenes(home: HomeModel):
    assert home.domains == ("climate", "cover", "light", "lock", "switch")


def test_entity_lookup(home: HomeModel):
    e = home.entity_by_id("light.reading_lamp")
    assert isinstance(e, Entity)
    assert e.aliases == ("lamp",)
    assert home.entity_by_id("light.none") is None
