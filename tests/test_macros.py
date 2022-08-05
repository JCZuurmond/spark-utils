from __future__ import annotations

import dataclasses
import os
from types import TracebackType
from typing import Any

import dbt.tracking
import pytest
from _pytest.fixtures import SubRequest
from dbt.adapters.factory import get_adapter, register_adapter, AdapterContainer
from dbt.clients.jinja import MacroGenerator
from dbt.config.runtime import RuntimeConfig
from dbt.context import providers
from dbt.contracts.connection import ConnectionState
from dbt.contracts.graph.manifest import Manifest
from dbt.adapters.spark.connections import (
    SparkConnectionManager,
    PyodbcConnectionWrapper,
)
from dbt.parser.manifest import ManifestLoader
from dbt.tracking import User
from pyspark.sql import DataFrame, Row, SparkSession


dbt.tracking.active_user = User(os.getcwd())


class Cursor:
    """
    Mock a pyodbc cursor.

    Source
    ------
    https://github.com/mkleehammer/pyodbc/wiki/Cursor
    """

    def __init__(self) -> None:
        self._df: DataFrame | None = None
        self._rows: list[Row] | None = None

    def __enter__(self) -> Cursor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: Exception | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        self.close()
        return True

    @property
    def description(
        self,
    ) -> list[tuple[str, str, None, None, None, None, bool]]:
        """
        Get the description.

        Returns
        -------
        out : list[tuple[str, str, None, None, None, None, bool]]
            The description.

        Source
        ------
        https://github.com/mkleehammer/pyodbc/wiki/Cursor#description
        """
        if self._df is None:
            description = list()
        else:
            description = [
                (
                    field.name,
                    field.dataType.simpleString(),
                    None,
                    None,
                    None,
                    None,
                    field.nullable,
                )
                for field in self._df.schema.fields
            ]
        return description

    def close(self) -> None:
        """
        Close the connection.

        Source
        ------
        https://github.com/mkleehammer/pyodbc/wiki/Cursor#close
        """
        self._df = None
        self._rows = None

    def execute(self, sql: str, *parameters: Any) -> None:
        """
        Execute a sql statement.

        Parameters
        ----------
        sql : str
            Execute a sql statement.
        *parameters : Any
            The parameters.

        Raises
        ------
        NotImplementedError
            If there are parameters given. We do not format sql statements.

        Source
        ------
        https://github.com/mkleehammer/pyodbc/wiki/Cursor#executesql-parameters
        """
        if len(parameters) > 0:
            raise NotImplementedError("Formatting sql statement is not implemented.")
        spark_session = SparkSession.builder.getOrCreate()
        self._df = spark_session.sql(sql)

    def fetchall(self) -> list[Row] | None:
        """
        Fetch all data.

        Returns
        -------
        out : list[Row] | None
            The rows.

        Source
        ------
        https://github.com/mkleehammer/pyodbc/wiki/Cursor#fetchall
        """
        if self._rows is None and self._df is not None:
            self._rows = self._df.collect()
        return self._rows

    def fetchone(self) -> Row | None:
        """
        Fetch the first output.

        Returns
        -------
        out : Row | None
            The first row.

        Source
        ------
        https://github.com/mkleehammer/pyodbc/wiki/Cursor#fetchone
        """
        if self._rows is None and self._df is not None:
            self._rows = self._df.collect()

        if self._rows is not None and len(self._rows) > 0:
            row = self._rows.pop(0)
        else:
            row = None

        return row


class Connection:
    """
    Mock a pyodbc connection.

    Source
    ------
    https://github.com/mkleehammer/pyodbc/wiki/Connection
    """

    def cursor(self) -> Cursor:
        """
        Get a cursor.

        Returns
        -------
        out : Cursor
            The cursor.
        """
        return Cursor()


class _SparkConnectionManager(SparkConnectionManager):
    @classmethod
    def open(cls, connection):
        handle = PyodbcConnectionWrapper(Connection())
        connection.handle = handle
        connection.state = ConnectionState.OPEN
        return connection


@dataclasses.dataclass(frozen=True)
class Args:
    project_dir: str = os.getcwd()


@pytest.fixture
def config() -> RuntimeConfig:
    # requires a profile in your project wich also exists in your profiles file
    config = RuntimeConfig.from_args(Args())
    return config


@pytest.fixture
def adapter(config: RuntimeConfig) -> AdapterContainer:
    register_adapter(config)
    adapter = get_adapter(config)

    connection_manager = _SparkConnectionManager(adapter.config)
    adapter.connections = connection_manager

    adapter.acquire_connection()

    return adapter


@pytest.fixture
def manifest(
    adapter: AdapterContainer,
) -> Manifest:
    manifest = ManifestLoader.get_full_manifest(adapter.config)
    return manifest


@pytest.fixture
def macro_generator(
    request: SubRequest, config: RuntimeConfig, manifest: Manifest
) -> MacroGenerator:
    macro = manifest.macros[request.param]
    context = providers.generate_runtime_macro_context(
        macro, config, manifest, macro.package_name
    )
    macro_generator = MacroGenerator(macro, context)
    return macro_generator


@pytest.mark.parametrize(
    "macro_generator", ["macro.spark_utils.get_tables"], indirect=True
)
def test_create_table(
    spark_session: SparkSession, macro_generator: MacroGenerator
) -> None:
    expected_table = "default.example"
    spark_session.sql(f"CREATE TABLE {expected_table} (id int) USING parquet")
    tables = macro_generator()
    assert tables == [expected_table]
