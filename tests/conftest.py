import pytest

from sales_pipeline.data_generator import generate
from sales_pipeline.tiering.features import frames_from_source_tables


@pytest.fixture(scope="session")
def source_tables():
    return generate(n_customers=300, n_products=40, seed=11)


@pytest.fixture(scope="session")
def mart_frames(source_tables):
    return frames_from_source_tables(source_tables)
