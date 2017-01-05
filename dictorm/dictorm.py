"""
What if you could insert a Python dictionary into the database?  DictORM allows
you to select/insert/update rows of a database as if they were Python
Dictionaries.
"""
from json import dumps
try: # pragma: no cover
    from dictorm.__version__ import __version__
except ImportError: # pragma: no cover
    from .__version__ import __version__

db_package_imported = False
try: # pragma: no cover
    from psycopg2.extras import DictCursor
    db_package_imported = True
except ImportError: # pragma: no cover
    pass

try: # pragma: no cover
    import sqlite3
    db_package_imported = True
except ImportError: # pragma: no cover
    pass

if not db_package_imported: # pragma: no cover
    raise ImportError('Failed to import psycopg2 or sqlite3.  These are the only supported Databases and you must import one of them')


__all__ = ['DictDB', 'Table', 'Dict', 'NoPrimaryKey',
    'UnexpectedRows', 'ResultsGenerator', 'column_value_pairs', '__version__',
    '__doc__']

class NoPrimaryKey(Exception): pass
class UnexpectedRows(Exception): pass

def operator_kinds(o):
    if o in (tuple, list):
        return ' IN '
    return '='


def column_value_pairs(kind, d, join_str=', ', prefix=''):
    """
    Create a string of SQL that will instruct a Psycopg2 DictCursor to
    interpolate the dictionary's keys into a SELECT or UPDATE SQL query.

    If old is True, prefix all values with old_ .  This is used to change
    primary key values.

    Example 1:
        >>> column_value_pairs({'id':10, 'person':'Dave'})
        id=%(id)s, person=%(person)s

    Example 2:
        >>> column_value_pairs(('id', 'person'))
        id=%(id)s, person=%(person)s

    Example 3:
        >>> column_value_pairs({'id':(10,11,13), 'group':'group'}, ' AND ')
        group=%(group)s AND id IN %(id)s

    Example 4:
        >>> column_value_pairs({'id':12, 'person':'Dave'}, prefix='old_')
        id=%(old_id)s, person=%(old_person)s
    """
    ret = ''
    final_item = len(d)-1
    for idx, key in enumerate(sorted(d)):
        ret += str(key) + operator_kinds(type(d[key] if type(d) == dict else type(key)))
        if kind == 'sqlite3':
            if type(d) == dict and type(d[key]) in (list, tuple):
                ret += '('+','.join([str(i) for i in d[key]])+')'
            else:
                ret += ':' + prefix + key
        elif kind == 'postgresql':
            ret += '%('+ prefix + key +')s'
        if idx != final_item:
            ret += join_str
    return ret


def insert_column_value_pairs(kind, d):
    """
    Create a string of SQL that will instruct a Psycopg2 DictCursor to
    interpolate the dictionary's keys into a INSERT SQL query.

    Example:
        >>> insert_column_value_pairs({'id':10, 'person':'Dave'})
        (id, person) VALUES (%(id)s, %(person)s)
    """
    d = sorted(d)
    if kind == 'sqlite3':
        return '({}) VALUES ({})'.format(
                ', '.join(d),
                ', '.join([':'+str(i) for i in d]),
                )
    else:
        return '({}) VALUES ({})'.format(
                ', '.join(d),
                ', '.join(['%('+str(i)+')s' for i in d]),
                )


def json_dicts(d):
    """
    Convert all dictionaries contained in this object into JSON strings.
    """
    for key, value in d.items():
        if type(value) == dict:
            d[key] = dumps(value)
    return d


class DictDB(dict):
    """
    Get all the tables from the provided psycopg2 connection.  Create a
    Table for that table, and keep it in this instance using the table's
    name as a key.

    >>> db =DictDB(your_db_connection)
    >>> db['table1']
    Table('table1')

    >>> db['other_table']
    Table('other_table')

    If your tables have changed while your DictDB instance existed, you can call
    DictDB.refresh_tables() to have it rebuild all Table objects.
    """

    def __init__(self, db_conn):
        self.conn = db_conn
        if type(db_conn) == sqlite3.Connection:
            self.kind = 'sqlite3'
        else:
            self.kind = 'postgresql'

        if self.kind == 'sqlite3':
            # row_factory using builtin Row which acts like a dictionary
            self.conn.row_factory = sqlite3.Row
            self.curs = self.conn.cursor()
        elif self.kind == 'postgresql':
            # using builtin DictCursor which gets/inserts/updates using
            # dictionaries
            self.curs = self.conn.cursor(cursor_factory=DictCursor)

        self.refresh_tables()
        super(DictDB, self).__init__()


    def _list_tables(self):
        if self.kind == 'sqlite3':
            self.curs.execute('SELECT name FROM sqlite_master WHERE type = "table"')
        else:
            self.curs.execute('''SELECT DISTINCT table_name
                    FROM information_schema.columns
                    WHERE table_schema='public' ''')
        return self.curs.fetchall()


    def refresh_tables(self):
        if self.keys():
            # Reset this DictDB because it contains old tables
            super(DictDB, self).__init__()
        for table in self._list_tables():
            if self.kind == 'sqlite3':
                self[table['name']] = Table(table['name'], self)
            else:
                self[table['table_name']] = Table(table['table_name'], self)


class ResultsGenerator:
    """
    This class replicates a Generator, it mearly adds the ability to
    get the len() of the generator (the rowcount of the last query run).
    This method should only be returned by Table.get_where and
    Table.get_one.

    Really, just use this class as if it were a generator unless you want
    a count.
    """

    def __init__(self, query, vars, table):
        self.query = query
        self.vars = vars
        self.table = table
        # This needs its own generator in case the usual cursor is used to
        # Update/Delete/Insert, overwriting the results of this query.
        if self.table.db.kind == 'sqlite3':
            self.curs = table.db.conn.cursor()
        elif self.table.db.kind == 'postgresql':
            self.curs = table.db.conn.cursor(cursor_factory=DictCursor)


    def __iter__(self): return self


    def __next__(self):
        self._execute_once()
        d = self.curs.fetchone()

        if not d:
            raise StopIteration
        # Convert returned dictionary to a Dict
        d = self.table(d)
        d._in_db = True
        return d


    def _execute_once(self):
        """
        Execute the query only once
        """
        if self.query:
            self.curs.execute(self.query, self.vars)
            self.query = None


    # for python 2.7
    next = __next__


    def __len__(self):
        self._execute_once()
        if self.table.db.kind == 'sqlite3':
            # sqlite3's cursor.rowcount doesn't support select statements
            return 0
        return self.curs.rowcount



class Table(object):
    """
    A representation of a DB table.  You will primarily retrieve rows
    (Dicts) from the database using the Table.get_where method.

    Insert into this table:

    >>> your_table(some_column='some value', other=False)
    {'some_column':'some value', 'other':False}

    Get all rows that need to be updated:

    >>> list(table.get_where(outdated=True))
    [Dict(), Dict(), Dict(), Dict()]

    Get a single row (will raise an UnexpectedRow error if more than one
    row could have been returned):

    >>> table.get_one(id=12)
    Dict()
    >>> table.get_one(manager_id=14)
    Dict()

    You can reference another table using setitem.  Link to an employee's
    manager using the manager's id, and the employee's manager_id.

    >>> Person['manager'] = Person['manager_id'] == Person['id']
    >>> Person['manager']
    Dict()

    Reference a manager's subordinates using their collective manager_id's
    (Use > instead of "in" because __contains__'s value is overwritten by
    python):

    >>> Person['subordinates'] = Person['id'] > Person['manager_id']
    >>> list(Person['manager'])
    [Dict(), Dict()]

    Table.get_where returns a generator object, this makes it so you
    won't have an entire table's object in memory at once, they are
    generated when gotten:

    >>> Person['subordinates']
    ResultsGenerator()
    >>> for sub in Person['subordinates']:
    >>>     print(sub)
    Dict()
    Dict()
    Dict()

    Get a count of all rows in this table:

    >>> Person.count()
    3
    """

    def __init__(self, table_name, db):
        self.name = table_name
        self.db = db
        self.curs = db.curs
        self.pks = []
        self.refs = {}
        self._set_pks()
        self.order_by = None


    def _set_pks(self):
        """
        Get a list of Primary Keys set for this table in the DB.
        """
        if self.db.kind == 'sqlite3':
            self.curs.execute('pragma table_info(%s)' % self.name)
            self.pks = [i['name'] for i in self.curs.fetchall() if i['pk']]

        elif self.db.kind == 'postgresql':
            self.curs.execute('''SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid
                    AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = '%s'::regclass
                    AND i.indisprimary;''' % self.name)
            self.pks = [i[0] for i in self.curs.fetchall()]


    def __repr__(self): # pragma: no cover
        return 'Table({}, {})'.format(self.name, self.pks)


    def __call__(self, *a, **kw):
        """
        Used to insert a row into this table.
        """
        d = Dict(self, *a, **kw)
        return self._add_references(d)


    def _pk_value_pairs(self, join_str=' AND ', prefix=''):
        return column_value_pairs(self.db.kind, self.pks, join_str, prefix)


    def get_where(self, *a, **kw):
        """
        Get all rows as Dicts where values are as specified.  This always
        returns a generator-like object ResultsGenerator.  You can get the
        length of that generator see ResultsGenerator.count.

        If you provide only arguments, they will be paired in their respective
        order to the primary keys defined for this table.  If the primary keys
        of this table was (id,) only:

            get_where(4) is equal to get_where(id=4)

            get_where(4, 5) would raise a NoPrimaryKey error because there is
                            only one primary key.

        Primary keys are defined automatically during the init of the Table,
        but you can overwrite that by simply changing the value:

        >>> your_table.pks = ['id', 'some_column', 'whatever_you_want']

            get_where(4, 5, 6) is now equal to get_where(id=4, some_column=5,
                                                    whatever_you_want=6)

        If there were two primary keys, such as in a join table (id, group):

            get_where(4, 5) is equal to get_where(id=4, group=5)

        You cannot use this method without primary keys, unless you specify
        the column you are matching.

        >>> get_where(some_column=83)
        ResultsGenerator()

        >>> get_where(4) # no primary keys defined!
        NoPrimaryKey()

        """
        order_by = None
        if self.order_by:
            order_by = self.order_by
        elif self.pks:
            order_by = self.pks[0]

        if a and len(a) == 1 and type(a[0]) == dict:
            # A single dictionary has been passed as an argument, use it as
            # the keyword arguments.
            kw = a[0]
        elif a:
            if not self.pks:
                raise NoPrimaryKey('No Primary Key(s) specified for '+str(self))
            kw = dict(zip(self.pks, a))

        # Build out the query using user-provideded data, and data gathered
        # from the DB.
        query = 'SELECT * FROM {table} '
        if kw:
            query += 'WHERE {wheres} '
        if order_by:
            query += 'ORDER BY {order_by}'
        query = query.format(
                table=self.name,
                wheres=column_value_pairs(self.db.kind, kw, ' AND '),
                order_by=order_by
            )
        return ResultsGenerator(query, kw, self)


    def get_one(self, *a, **kw):
        """
        Get a single row as a Dict from the Database that matches provided
        to this method.  See Table.get_where for more details.

        If more than one row could be returned, this will raise an
        UnexpectedRows error.
        """
        l = list(self.get_where(*a, **kw))
        if len(l) > 1:
            raise UnexpectedRows('More than one row selected.')
        return l[0]


    def _add_references(self, d):
        for ref_name in self.refs:
            d[ref_name] = None
        return d


    def count(self):
        """
        Get the count of rows in this table.
        """
        self.curs.execute('SELECT COUNT(*) FROM {table}'.format(
            table=self.name))
        return int(self.curs.fetchone()[0])


    def __setitem__(self, ref_name, value):
        if len(value) == 3:
            my_column, sub_reference, their_refname = value
            self.refs[ref_name] = (my_column, sub_reference, their_refname)
        else:
            my_column, table, their_column, many = value
            self.refs[ref_name] = (
                    self, my_column, table, their_column, many)


    def __getitem__(self, key):
        return Reference(self, key)



class Reference(object):
    """
    This class facilitates creating relationships between Tables by using
    == and >.

    I would rather use "in" instead of ">", but "__contains__" overwrites what
    is returned and only returns a True/False value. :(
    """

    def __init__(self, table, column):
        self.table = table
        self.column = column

    def __repr__(self): # pragma: no cover
        return 'Reference({}, {})'.format(self.table.name, self.column)

    def __eq__(ref1, ref2):
        return (ref1.column, ref2.table, ref2.column, False)

    def __gt__(ref1, ref2):
        return (ref1.column, ref2.table, ref2.column, True)

    def substratum(self, column):
        return (self.column, self.table[self.column], column)



class Dict(dict):
    """
    This behaves exactly like a dictionary, you may update your database row
    (this Dict instance) using update or simply by setting an item.  After
    you make changes, be sure to call Dict.flush on the instance of this
    object to send your changes to the DB.  Your changes will not be commited
    or rolled-back, you must do that.

    This relies heavily on primary keys and they should be specified.  Really,
    your tables should have a primary key of some sort.  If not, this will
    pretty much be a read-only object.

    You can change the primary key of an instance.

    Use setitem:
    >>> d['manager_id'] = 4

    Use an update:
    >>> d.update({'manager_id':4})

    Update using another Dict:
    >>> d1.update(d2.remove_pks())

    Make sure to send your changes to the database:
    >>> d.flush()

    Remove a row:
    >>> d.delete()
    """

    def __init__(self, table, *a, **kw):
        self._table = table
        self._in_db = False
        self._curs = table.db.curs
        super(Dict, self).__init__(*a, **kw)
        self._old = self.remove_refs()


    def flush(self):
        """
        Insert this dictionary into it's table if its no yet in the Database,
        or Update it's row if it is already in the database.  This method
        relies heaviliy on the primary keys of the row's respective table.  If
        no primary keys are specified, this method will not function!

        All original column/values will bet inserted/set by this method.  If
        a reference sub-dictionary has been defined, it will NOT be submitted to
        the DB.  However, the reference's respective reference column will be
        updated.
        """
        if not self._in_db:
            d = json_dicts(self.remove_refs())
            query = 'INSERT INTO {table} {cvp}'.format(
                    table=self._table.name,
                    cvp=insert_column_value_pairs(self._table.db.kind,
                        self.remove_refs())
                )

            if self._table.db.kind == 'postgresql':
                query += ' RETURNING *'

            # Run the insert query, interpolating the values of this dictionary
            # into the query.
            self._curs.execute(query, d)
            self._in_db = True

            if self._table.db.kind == 'sqlite3':
                # Get the last inserted row, postgresql uses RETURNING
                self._curs.execute('''SELECT * FROM {table} WHERE
                        rowid = last_insert_rowid()'''.format(
                    table=self._table.name))
        else:
            if not self._table.pks:
                raise NoPrimaryKey(
                        'Cannot update to {}, no primary keys defined.'.format(
                    self._table))
            combined = self.remove_refs()
            combined.update(dict([('old_'+k,v) for k,v in self._old.items()]))
            combined = json_dicts(combined)
            query = 'UPDATE {table} SET {cvp} WHERE {pvp}'.format(
                    table=self._table.name,
                    cvp=column_value_pairs(self._table.db.kind,
                        self.remove_refs()),
                    pvp=self._table._pk_value_pairs(prefix='old_')
                    )

            if self._table.db.kind == 'postgresql':
                query += ' RETURNING *'

            self._curs.execute(query, combined)

            if self._table.db.kind == 'sqlite3':
                # Get the row that was just updated using the primary keys
                query = 'SELECT * FROM {table} WHERE {pvp}'.format(
                        table=self._table.name,
                        pvp=self._table._pk_value_pairs()
                    )
                self._curs.execute(query, combined)

        d = self._curs.fetchone()
        super(Dict, self).__init__(d)
        self._old = self.remove_refs()
        return self


    def delete(self):
        """
        Delete this row from it's table in the database.  Requires primary
        keys to be specified.
        """
        self._curs.execute('DELETE FROM {table} WHERE {pvp}'.format(
                table=self._table.name,
                pvp=self._table._pk_value_pairs()),
            self
            )


    def remove_pks(self):
        """
        Return a dictionary without the primary keys that are associated with
        this Dict in the Database.  This should be used when doing an update
        of another Dict.
        """
        return dict([(k,v) for k,v in self.items() if k not in self._table.pks])


    def remove_refs(self):
        """
        Return a dictionary without the key/value(s) added by a reference.  They
        should never be sent in the query to the Database.
        """
        return dict([(k,v) for k,v in self.items() if k not in self._table.refs])


    def __getitem__(self, key):
        """
        Get the provided "key" from the dictionary.  If the key refers to a
        referenced row, get that row first.
        """
        ref = self._table.refs.get(key)
        sub_reference = False
        if ref:
            if len(ref) == 3:
                sub_reference = True
                # This reference is linking two references, get the value of the
                # regular reference using usual means, then pull the
                # sub-reference.
                my_column, table, their_sub_ref = ref
                ref = self._table.refs[my_column]

            my_table, my_column, table, their_column, many = ref
            wheres = {their_column:self[my_column]}
            if many:
                val = table.get_where(**wheres)
            else:
                try:
                    val = table.get_one(**wheres)
                except IndexError:
                    # No results returned, must not be set
                    val = None

            if sub_reference and many:
                val = [i[their_sub_ref] for i in val]
            elif sub_reference:
                val = val[their_sub_ref]

            super(Dict, self).__setitem__(key, val)
            return val
        return super(Dict, self).__getitem__(key)


    __getitem__.__doc__ += dict.__getitem__.__doc__



