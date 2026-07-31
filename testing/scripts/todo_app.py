class TodoApp:
    def __init__(self):
        self.todos = []
        self.next_id = 1

    def add(self, title, priority="medium"):
        # TODO: validate priority is one of low/medium/high
        todo = {"id": self.next_id, "title": title, "priority": priority, "done": False}
        self.todos.append(todo)
        self.next_id += 1
        return todo

    def complete(self, todo_id):
        # TODO: raise error if todo not found instead of silent fail
        for todo in self.todos:
            if todo["id"] == todo_id:
                todo["done"] = True
                return todo

    def delete(self, todo_id):
        # TODO: implement soft delete instead of hard delete
        self.todos = [t for t in self.todos if t["id"] != todo_id]

    def list_all(self):
        return self.todos

    def list_pending(self):
        # TODO: add sorting by priority (high > medium > low)
        return [t for t in self.todos if not t["done"]]

    def list_completed(self):
        return [t for t in self.todos if t["done"]]

    def search(self, query):
        # TODO: make search case-insensitive
        return [t for t in self.todos if query in t["title"]]

    def update_priority(self, todo_id, priority):
        # TODO: validate priority value
        for todo in self.todos:
            if todo["id"] == todo_id:
                todo["priority"] = priority
                return todo

    def get_stats(self):
        # TODO: add average completion time tracking
        total = len(self.todos)
        done = sum(1 for t in self.todos if t["done"])
        return {"total": total, "done": done, "pending": total - done}
