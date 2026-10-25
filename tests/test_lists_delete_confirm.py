from tests.chrome import needs_node
from tests.test_lists_page import _node


@needs_node
def test_delete_text_names_screens_that_show_the_list():
    result = _node(
        "const l = {id: 5, name: 'Shop'};"
        "const s = (name, ids) => ({name: name, widgets: ids.map(i => ({kind: 'lists', list_id: i}))});"
        "console.log(JSON.stringify(["
        "L.deleteText(l, []),"
        "L.deleteText(l, [s('Notes', [null, 6])]),"
        "L.deleteText(l, [s('Kitchen', [5])]),"
        "L.deleteText(l, [s('A', [5]), s('B', [4]), s('C', [5, 5])]),"
        "L.deleteText(l, [s('A', [5]), s('B', [5]), s('C', [5])])]))"
    )
    assert result == [
        'Delete "Shop" and everything on it?',
        'Delete "Shop" and everything on it?',
        'Delete "Shop" and everything on it? It is also removed from the TV screen "Kitchen".',
        'Delete "Shop" and everything on it? It is also removed from the TV screens "A" and "C".',
        'Delete "Shop" and everything on it? It is also removed from the TV screens "A", "B" and "C".',
    ]
