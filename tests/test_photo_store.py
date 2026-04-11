"""Tests for PhotoStore singleton."""

import pytest
from datetime import datetime
from georeel.core.photo_metadata import PhotoMetadata
from georeel.core.photo_store import PhotoStore


def _meta(path, ts=None, lat=None, lon=None):
    return PhotoMetadata(path=path, timestamp=ts, latitude=lat, longitude=lon)


class TestPhotoStoreSingleton:
    def test_instance_returns_same_object(self):
        s1 = PhotoStore.instance()
        s2 = PhotoStore.instance()
        assert s1 is s2


class TestPhotoStoreAdd:
    def test_add_single_photo(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        assert len(store.all()) == 1

    def test_add_deduplicates_by_path(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.add(_meta("/a.jpg"))
        assert len(store.all()) == 1

    def test_add_different_paths(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.add(_meta("/b.jpg"))
        assert len(store.all()) == 2

    def test_all_returns_copy(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        lst = store.all()
        lst.clear()  # modifying copy should not affect store
        assert len(store.all()) == 1


class TestPhotoStoreRemove:
    def test_remove_existing(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.add(_meta("/b.jpg"))
        store.remove("/a.jpg")
        paths = [p.path for p in store.all()]
        assert "/a.jpg" not in paths
        assert "/b.jpg" in paths

    def test_remove_nonexistent_no_error(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.remove("/nonexistent.jpg")  # should not raise
        assert len(store.all()) == 1

    def test_remove_all_then_empty(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.remove("/a.jpg")
        assert store.all() == []


class TestPhotoStoreClear:
    def test_clear_empties_store(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.add(_meta("/b.jpg"))
        store.clear()
        assert store.all() == []

    def test_clear_on_empty_store(self):
        store = PhotoStore.instance()
        store.clear()  # should not raise
        assert store.all() == []


class TestPhotoStoreUpdateTimestamp:
    def test_update_sets_timestamp(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        ts = datetime(2023, 5, 1, 10, 0)
        store.update_timestamp("/a.jpg", ts)
        photos = store.all()
        assert photos[0].timestamp == ts

    def test_update_preserves_gps(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg", lat=48.0, lon=2.0))
        store.update_timestamp("/a.jpg", datetime(2023, 1, 1))
        photos = store.all()
        assert photos[0].latitude == 48.0
        assert photos[0].longitude == 2.0

    def test_update_nonexistent_path_is_noop(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.update_timestamp("/b.jpg", datetime(2023, 1, 1))
        assert store.all()[0].timestamp is None


class TestPhotoStoreUpdateGps:
    def test_update_gps_sets_coords(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg"))
        store.update_gps("/a.jpg", 48.0, 2.0)
        photos = store.all()
        assert photos[0].latitude == 48.0
        assert photos[0].longitude == 2.0

    def test_update_gps_preserves_timestamp(self):
        ts = datetime(2023, 6, 1, 9, 0)
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg", ts=ts))
        store.update_gps("/a.jpg", 51.5, -0.12)
        assert store.all()[0].timestamp == ts

    def test_update_gps_to_none(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg", lat=48.0, lon=2.0))
        store.update_gps("/a.jpg", None, None)
        photos = store.all()
        assert photos[0].latitude is None
        assert photos[0].longitude is None


class TestPhotoStoreProperties:
    def test_all_have_timestamp_true(self):
        store = PhotoStore.instance()
        ts = datetime(2023, 1, 1)
        store.add(_meta("/a.jpg", ts=ts))
        store.add(_meta("/b.jpg", ts=ts))
        assert store.all_have_timestamp is True

    def test_all_have_timestamp_false_partial(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg", ts=datetime(2023, 1, 1)))
        store.add(_meta("/b.jpg"))
        assert store.all_have_timestamp is False

    def test_all_have_timestamp_empty_store(self):
        store = PhotoStore.instance()
        assert store.all_have_timestamp is False

    def test_all_have_gps_true(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg", lat=1.0, lon=2.0))
        store.add(_meta("/b.jpg", lat=3.0, lon=4.0))
        assert store.all_have_gps is True

    def test_all_have_gps_false_missing_one(self):
        store = PhotoStore.instance()
        store.add(_meta("/a.jpg", lat=1.0, lon=2.0))
        store.add(_meta("/b.jpg"))
        assert store.all_have_gps is False

    def test_all_have_gps_false_empty_store(self):
        store = PhotoStore.instance()
        assert store.all_have_gps is False
