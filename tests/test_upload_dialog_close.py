from tests.test_upload_batches import _node, needs_node


@needs_node
def test_title_close_and_backdrop_close_upload_only_when_finished():
    r = _node(
        """
        await flush();
        pick([file("a.jpg")]);
        $("upload-backdrop").fire("click");
        $("upload-x").fire("click");
        const running = { open: !$("upload-dialog").hidden, xHidden: $("upload-x").hidden };
        finish("a.jpg", 200, { uploaded: [] });
        await flush();
        const done = { xHidden: $("upload-x").hidden };
        $("upload-backdrop").fire("click");
        done.closedByBackdrop = $("upload-dialog").hidden;
        pick([file("b.jpg")]);
        finish("b.jpg", 200, { uploaded: [] });
        await flush();
        $("upload-x").fire("click");
        const closedByX = $("upload-dialog").hidden;
        console.log(JSON.stringify({ running: running, done: done, closedByX: closedByX }));
        """
    )
    assert r["running"] == {"open": True, "xHidden": True}
    assert r["done"] == {"xHidden": False, "closedByBackdrop": True}
    assert r["closedByX"] is True
