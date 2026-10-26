import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import relationship
from strawberry_sqlalchemy_mapper import StrawberrySQLAlchemyLoader
from strawberry_sqlalchemy_mapper.pagination_cursor_utils import (
    encode_cursor_index,
)

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture
def many_to_one_tables(base):
    class Employee(base):
        __tablename__ = "employee"
        id = Column(Integer, autoincrement=True, primary_key=True)
        name = Column(String, nullable=False)
        department_id = Column(Integer, ForeignKey("department.id"))
        department = relationship("Department", back_populates="employees")
        __mapper_args__ = {"eager_defaults": True}

    class Department(base):
        __tablename__ = "department"
        id = Column(Integer, autoincrement=True, primary_key=True)
        name = Column(String, nullable=False)
        employees = relationship("Employee", back_populates="department")
        __mapper_args__ = {"eager_defaults": True}

    return Employee, Department


@pytest.fixture
def secondary_tables(base):
    EmployeeDepartmentJoinTable = Table(
        "employee_department_join_table",
        base.metadata,
        Column("employee_id", ForeignKey("employee.e_id"), primary_key=True),
        Column("department_id", ForeignKey("department.d_id"), primary_key=True),
    )

    class Employee(base):
        __tablename__ = "employee"
        e_id = Column(Integer, autoincrement=True, primary_key=True)
        name = Column(String, nullable=False)
        departments = relationship(
            "Department",
            secondary="employee_department_join_table",
            back_populates="employees",
        )

    class Department(base):
        __tablename__ = "department"
        d_id = Column(Integer, autoincrement=True, primary_key=True)
        name = Column(String, nullable=False)
        employees = relationship(
            "Employee",
            secondary="employee_department_join_table",
            back_populates="departments",
        )

    return Employee, Department


def test_loader_init():
    loader = StrawberrySQLAlchemyLoader(bind=None)
    assert loader._bind is None
    assert loader._loaders == {}


@pytest.mark.asyncio
async def test_loader_for(engine, base, sessionmaker, many_to_one_tables):
    Employee, Department = many_to_one_tables
    base.metadata.create_all(engine)

    with sessionmaker() as session:
        e1 = Employee(name="e1")
        e2 = Employee(name="e2")
        d1 = Department(name="d1")
        d2 = Department(name="d2")
        session.add(e1)
        session.add(e2)
        session.add(d1)
        session.add(d2)
        session.flush()

        e1.department = d2
        e2.department = d1
        session.commit()
        base_loader = StrawberrySQLAlchemyLoader(bind=session)
        paginated_loader = base_loader.loader_for(Employee.department.property)
        loader = paginated_loader.loader_for()
        assert loader.max_batch_size is None
        assert loader.cache is True
        assert not loader.cache_map.cache_map
        assert loader._loop is None
        assert loader.load_fn is not None

        key = tuple(
            getattr(e1, local.key) for local, _ in Employee.department.property.local_remote_pairs
        )
        department = await loader.load(key)
        assert department.name == "d2"

        paginated_loader = base_loader.loader_for(Department.employees.property)
        loader = paginated_loader.loader_for()

        employees = await loader.load((d2.id,))
        assert {e.name for e in employees} == {"e1"}


@pytest.mark.asyncio
async def test_loader_with_async_session(
    async_engine, base, async_sessionmaker, many_to_one_tables
):
    Employee, Department = many_to_one_tables
    async with async_engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)

    async with async_sessionmaker(expire_on_commit=False) as session:
        e1 = Employee(name="e1")
        e2 = Employee(name="e2")
        d1 = Department(name="d1")
        d2 = Department(name="d2")
        session.add(e1)
        session.add(e2)
        session.add(d1)
        session.add(d2)
        await session.flush()

        e1.department = d2
        e2.department = d1
        await session.commit()
        d2_id = d2.id
        department_loader_key = tuple(
            getattr(e1, local.key) for local, _ in Employee.department.property.local_remote_pairs
        )
    base_loader = StrawberrySQLAlchemyLoader(async_bind_factory=async_sessionmaker)
    loader = base_loader.loader_for(Employee.department.property).loader_for()

    department = await loader.load(department_loader_key)
    assert department.name == "d2"

    loader = base_loader.loader_for(Department.employees.property).loader_for()
    employees = await loader.load((d2_id,))
    assert {e.name for e in employees} == {"e1"}


@pytest.mark.xfail
@pytest.mark.asyncio
async def test_loader_for_secondary(engine, base, sessionmaker, secondary_tables):
    Employee, Department = secondary_tables
    base.metadata.create_all(engine)

    with sessionmaker() as session:
        e1 = Employee(name="e1")
        e2 = Employee(name="e2")
        d1 = Department(name="d1")
        d2 = Department(name="d2")
        session.add(e1)
        session.add(e2)
        session.add(d1)
        session.add(d2)
        session.flush()

        e1.departments.append(d1)
        e1.departments.append(d2)
        e2.departments.append(d2)
        session.commit()

        base_loader = StrawberrySQLAlchemyLoader(bind=session)
        loader = base_loader.loader_for(Employee.departments.property).loader_for()

        key = tuple(
            [
                getattr(e1, local.key)
                for local, _ in Employee.departments.property.local_remote_pairs
            ]
        )
        departments = await loader.load(key)
        assert {d.name for d in departments} == {"d1", "d2"}


@pytest.mark.asyncio
async def test_loader_for_pagination_valid(engine, base, sessionmaker, many_to_one_tables):
    Employee, Department = many_to_one_tables
    base.metadata.create_all(engine)

    with sessionmaker() as session:
        d1 = Department(
            name="d1",
            employees=[
                Employee(name="e1"),
                Employee(name="e2"),
                Employee(name="e3"),
                Employee(name="e4"),
            ],
        )
        d_empty = Department(name="empty")
        session.add(d1)
        session.add(d_empty)
        session.flush()
        session.commit()
        base_loader = StrawberrySQLAlchemyLoader(bind=session)
        paginated_loader = base_loader.loader_for(Department.employees.property)

        first_loader = paginated_loader.loader_for(first=2)
        assert not (await first_loader.load((d_empty.id,))), "d_empty returns no results"
        employees = await first_loader.load((d1.id,))
        assert {e.name for e in employees} == {"e1", "e2"}

        last_loader = paginated_loader.loader_for(last=2)
        assert not (await last_loader.load((d_empty.id,))), "d_empty returns no results"
        employees = await last_loader.load((d1.id,))
        assert {e.name for e in employees} == {"e3", "e4"}

        before_loader = paginated_loader.loader_for(before=encode_cursor_index(3))
        assert not (await before_loader.load((d_empty.id,))), "d_empty returns no results"
        employees = await before_loader.load((d1.id,))
        assert {e.name for e in employees} == {"e1", "e2", "e3"}

        after_loader = paginated_loader.loader_for(after=encode_cursor_index(0))
        assert not (await after_loader.load((d_empty.id,))), "d_empty returns no results"
        employees = await after_loader.load((d1.id,))
        assert {e.name for e in employees} == {"e2", "e3", "e4"}

        first_after_loader = paginated_loader.loader_for(first=2, after=encode_cursor_index(0))
        assert not (await first_after_loader.load((d_empty.id,))), "d_empty returns no results"
        employees = await first_after_loader.load((d1.id,))
        assert {e.name for e in employees} == {"e2", "e3"}

        last_before_loader = paginated_loader.loader_for(last=2, before=encode_cursor_index(2))
        assert not (await last_before_loader.load((d_empty.id,))), "d_empty returns no results"
        employees = await last_before_loader.load((d1.id,))
        assert {e.name for e in employees} == {"e1", "e2"}


@pytest.mark.asyncio
async def test_loader_for_pagination_invalid(engine, base, sessionmaker, many_to_one_tables):
    Employee, Department = many_to_one_tables
    base.metadata.create_all(engine)

    with sessionmaker() as session:
        d1 = Department(
            name="d1",
            employees=[
                Employee(name="e1"),
                Employee(name="e2"),
                Employee(name="e3"),
                Employee(name="e4"),
            ],
        )
        d_empty = Department(name="empty")
        session.add(d1)
        session.add(d_empty)
        session.flush()
        session.commit()
        base_loader = StrawberrySQLAlchemyLoader(bind=session)
        paginated_loader = base_loader.loader_for(Department.employees.property)

        first_last_loader = paginated_loader.loader_for(first=2, last=3)
        with pytest.raises(ValueError, match="Cannot provide"):
            await first_last_loader.load((d1.id,))
        with pytest.raises(ValueError, match="Cannot provide"):
            await first_last_loader.load((d_empty.id,))

        first_before_loader = paginated_loader.loader_for(first=2, before=encode_cursor_index(3))
        with pytest.raises(ValueError, match="Cannot provide"):
            await first_before_loader.load((d1.id,))
        with pytest.raises(ValueError, match="Cannot provide"):
            await first_before_loader.load((d_empty.id,))

        last_after_loader = paginated_loader.loader_for(last=2, after=encode_cursor_index(0))
        with pytest.raises(ValueError, match="Cannot provide"):
            await last_after_loader.load((d1.id,))
        with pytest.raises(ValueError, match="Cannot provide"):
            await last_after_loader.load((d_empty.id,))


def test_get_loader_from_info_missing_loader():
    from unittest.mock import Mock

    err_msg = "missing a required 'sqlalchemy_loader'"

    from strawberry_sqlalchemy_mapper import (
        MissingSQLAlchemyLoaderError,
        StrawberrySQLAlchemyLoader,
    )
    from strawberry_sqlalchemy_mapper.mapper import _get_loader_from_info

    # 1. Context is None
    info_none = Mock()
    info_none.context = None
    with pytest.raises(MissingSQLAlchemyLoaderError, match=err_msg):
        _get_loader_from_info(info_none)

    # 2. Context is an empty dict
    info_dict_empty = Mock()
    info_dict_empty.context = {}
    with pytest.raises(MissingSQLAlchemyLoaderError, match=err_msg):
        _get_loader_from_info(info_dict_empty)

    # 3. Context is an object without the attribute
    class CustomContext:
        pass

    info_obj_missing = Mock()
    info_obj_missing.context = CustomContext()
    with pytest.raises(MissingSQLAlchemyLoaderError, match=err_msg):
        _get_loader_from_info(info_obj_missing)

    # 4. Context has sqlalchemy_loader explicitly set to None
    info_dict_none = Mock()
    info_dict_none.context = {"sqlalchemy_loader": None}
    with pytest.raises(MissingSQLAlchemyLoaderError, match=err_msg):
        _get_loader_from_info(info_dict_none)

    # 5. Success case with dict context
    dummy_loader = Mock(spec=StrawberrySQLAlchemyLoader)
    info_dict_valid = Mock()
    info_dict_valid.context = {"sqlalchemy_loader": dummy_loader}
    assert _get_loader_from_info(info_dict_valid) is dummy_loader

    # 6. Success case with object context
    class ValidContext:
        def __init__(self, loader):
            self.sqlalchemy_loader = loader

    info_obj_valid = Mock()
    info_obj_valid.context = ValidContext(dummy_loader)
    assert _get_loader_from_info(info_obj_valid) is dummy_loader
