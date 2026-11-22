"""Object filters shared by the main Alembic migration environment."""


def exclude_book_commerce(obj, name, type_, reflected, compare_to):
    def is_book_commerce_table(table_name):
        return table_name.startswith('bc_') or table_name == 'alembic_version_book_commerce'

    if type_ == 'table':
        return not is_book_commerce_table(name)
    table = getattr(obj, 'table', None)
    return table is None or not is_book_commerce_table(table.name)
