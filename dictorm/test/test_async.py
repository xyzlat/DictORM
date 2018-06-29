import asyncio

import aiopg
import pytest

from dictorm.async import DictDB


schema = '''
    CREATE TABLE person (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100),
        other INTEGER,
        manager_id INTEGER REFERENCES person(id)
    );
    CREATE TABLE department (
        id SERIAL PRIMARY KEY,
        name TEXT
    );
    CREATE TABLE person_department (
        person_id INTEGER REFERENCES person(id),
        department_id INTEGER REFERENCES department(id),
        PRIMARY KEY (person_id, department_id)
    );
    CREATE TABLE car (
        id SERIAL PRIMARY KEY,
        license_plate TEXT,
        name TEXT,
        person_id INTEGER REFERENCES person(id)
    );
    ALTER TABLE person ADD COLUMN car_id INTEGER REFERENCES car(id);
    CREATE TABLE no_pk (foo VARCHAR(10));
    CREATE TABLE station (
        person_id INTEGER
    );
    CREATE TABLE possession (
        id SERIAL PRIMARY KEY,
        person_id INTEGER,
        description JSONB
    );
'''

dsn = 'dbname=postgres user=postgres password=dictorm host=localhost port=54321'


async def _set_up():
    pool = await aiopg.create_pool(dsn)
    async with pool.acquire() as conn:
        curs = await conn.cursor()
        await curs.execute('''DROP SCHEMA public CASCADE;
                        CREATE SCHEMA public;
                        GRANT ALL ON SCHEMA public TO postgres;
                        GRANT ALL ON SCHEMA public TO public;''')
        await curs.execute(schema)
        conn.commit()
    db = await DictDB(pool).init()
    return db


@pytest.mark.asyncio
async def test_basic():
    db = await _set_up()
    Person = db['person']

    # Create a person, their ID should be set after flush
    bob = Person(name='Bob')
    assert bob == {'name': 'Bob'}
    await bob.flush()
    assert set(bob.items()).issuperset({('name', 'Bob'), ('id', 1)})

    # Name change sticks after flush
    bob['name'] = 'Steve'
    await bob.flush()
    assert set(bob.items()).issuperset({('name', 'Steve'), ('id', 1)})

    # Create a second person
    alice = await Person(name='Alice').flush()
    assert set(alice.items()).issuperset({('name', 'Alice'), ('id', 2)})

    # Can get all people
    persons = Person.get_where()
    for person, expected in zip(persons, [bob, alice]):
        assert person._table == expected._table
        assert person == expected

    # Delete Bob, a single person remains untouched
    await bob.delete()
    persons = list(Person.get_where())
    assert persons == [alice]
    assert persons[0]['id'] == 2

    # Can get all people
    persons = list(Person.get_where(Person['id'] == 2))
    assert persons[0]['id'] == 2

    # Create a new person, can use greater-than filter
    steve = await Person(name='Steve').flush()
    persons = list(await Person.get_where(Person['id'] > 2))
    assert [steve] == persons


@pytest.mark.asyncio
async def test_multi_insert():
    db = await _set_up()
    Person = db['person']
    from uuid import uuid4

    NUMBER = 10
    coros = (Person(name=str(uuid4())).flush() for _ in range(NUMBER))
    await asyncio.gather(*coros)
    curs = db.get_cursor()
    curs.execute('select count(*) from person')
    assert curs.fetchone() == [NUMBER]
    assert len(list(Person.get_where())) == NUMBER


@pytest.mark.asyncio
async def test_relations():
    db = await _set_up()
    Person, Department, PD = db['person'], db['department'], db['person_department']
    PD['department'] = PD['department_id'] == Department['id']
    PD['person'] = PD['person_id'] == Person['id']
    Person['person_departments'] = Person['id'].many(PD['person_id'])
    Person['departments'] = Person['person_departments'].substratum('department')

    # Bob is in Sales, but not HR
    bob = await Person(name='Bob').flush()
    aly = await Person(name='Aly').flush()
    sales = await Department(name='Sales').flush()
    hr = await Department(name='HR').flush()
    bob_sales = await PD(person_id=await bob['id'], department_id=await sales['id']).flush()
    assert list(await bob['person_departments']) == [bob_sales]

    # Aly is in HR and Sales
    aly_hr = await PD(person_id=aly['id'], department_id=hr['id']).flush()
    aly_sales = await PD(person_id=aly['id'], department_id=sales['id']).flush()
    assert list(await bob['person_departments']) == [bob_sales]
    assert list(await aly['person_departments']) == [aly_hr, aly_sales]

    assert await aly['departments'] == [hr, sales]
    # Bob is still only in Sales
    assert await bob['departments'] == [sales]

    # Get all the people in each department
    steve = await Person(name='Steve').flush()
    await PD(person_id=steve['id'], department_id=sales['id']).flush()
    Department['person_departments'] = Department['id'].many(PD['department_id'])
    Department['persons'] = Department['person_departments'].substratum('person')
    assert await sales['persons'] == [bob, aly, steve]
    assert await hr['persons'] == [aly]


@pytest.mark.asyncio
async def test_searching():
    db = await _set_up()
    Person, Department, PD = db['person'], db['department'], db['person_department']
    PD['department'] = PD['department_id'] == Department['id']
    PD['person'] = PD['person_id'] == Person['id']
    Person['person_departments'] = Person['id'].many(PD['person_id'])
    Person['departments'] = Person['person_departments'].substratum('department')

    # Bob is in Sales, but not HR
    bob = await Person(name='Bob').flush()
    aly = await Person(name='Aly').flush()
    steve = await Person(name='Steve').flush()
    frank = await Person(name='Frank').flush()

    persons = Person.get_where(Person['id'] > 1)
    assert list(persons) == [aly, steve, frank]

    persons2 = persons.refine(Person['id'] < 4)
    assert list(persons2) == [aly, steve]
