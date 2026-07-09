import pytest


@pytest.fixture
def sample_unified_diff() -> str:
    """一份标准的 unified diff 示例文本，用于测试 DiffParser。"""
    return """diff --git a/hello.py b/hello.py
index abc123..def456 100644
--- a/hello.py
+++ b/hello.py
@@ -1,4 +1,4 @@
 def greet(name):
-    return "Hello, " + name
+    return f"Hello, {name}!"

 def farewell(name):
     return "Bye, " + name
diff --git a/new_file.py b/new_file.py
new file mode 100644
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+def add(a, b):
+    return a + b
+
diff --git a/logo.png b/logo.png
index abc..def 100644
Binary files a/logo.png and b/logo.png differ
"""
