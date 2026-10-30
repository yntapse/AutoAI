"""FastAPI routers mounted by main.py.

Keeping routers in their own package means new endpoints do not grow main.py
and do not sit inside the blast radius of the existing datasource block.

Nothing here imports main at module level - see routers/datamodel.py.
"""
