class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        raise NotImplementedError

    def pop(self):
        raise NotImplementedError

    def peek(self):
        raise NotImplementedError

    def is_empty(self):
        raise NotImplementedError

    def size(self):
        raise NotImplementedError


class Queue:
    def __init__(self):
        self._items = []

    def enqueue(self, item):
        raise NotImplementedError

    def dequeue(self):
        raise NotImplementedError

    def front(self):
        raise NotImplementedError

    def is_empty(self):
        raise NotImplementedError

    def size(self):
        raise NotImplementedError


class LinkedList:
    class Node:
        def __init__(self, data):
            self.data = data
            self.next = None

    def __init__(self):
        self.head = None
        self._size = 0

    def append(self, data):
        raise NotImplementedError

    def prepend(self, data):
        raise NotImplementedError

    def delete(self, data):
        raise NotImplementedError

    def find(self, data):
        raise NotImplementedError

    def size(self):
        raise NotImplementedError

    def to_list(self):
        raise NotImplementedError
