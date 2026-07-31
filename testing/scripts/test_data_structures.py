from data_structures import Stack, Queue, LinkedList


def test_stack_push_pop():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.pop() == 2
    assert s.pop() == 1


def test_stack_peek():
    s = Stack()
    s.push(42)
    assert s.peek() == 42
    assert s.size() == 1


def test_stack_empty():
    s = Stack()
    assert s.is_empty()
    s.push(1)
    assert not s.is_empty()


def test_queue_enqueue_dequeue():
    q = Queue()
    q.enqueue("a")
    q.enqueue("b")
    assert q.dequeue() == "a"
    assert q.dequeue() == "b"


def test_queue_front():
    q = Queue()
    q.enqueue(10)
    assert q.front() == 10
    assert q.size() == 1


def test_queue_empty():
    q = Queue()
    assert q.is_empty()
    q.enqueue(1)
    assert not q.is_empty()


def test_linked_list_append():
    ll = LinkedList()
    ll.append(1)
    ll.append(2)
    ll.append(3)
    assert ll.to_list() == [1, 2, 3]


def test_linked_list_prepend():
    ll = LinkedList()
    ll.prepend(1)
    ll.prepend(2)
    assert ll.to_list() == [2, 1]


def test_linked_list_delete():
    ll = LinkedList()
    ll.append(1)
    ll.append(2)
    ll.append(3)
    ll.delete(2)
    assert ll.to_list() == [1, 3]


def test_linked_list_find():
    ll = LinkedList()
    ll.append(10)
    ll.append(20)
    assert ll.find(10) is not None
    assert ll.find(99) is None


def test_linked_list_size():
    ll = LinkedList()
    assert ll.size() == 0
    ll.append(1)
    assert ll.size() == 1
