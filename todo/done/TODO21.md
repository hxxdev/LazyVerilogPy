### Fix AutoWire Behavior:

If return type of a function is declared as array type of struct type which is invalid syntax, function return assigned to LHS should be      
shown "Failed to add:" when running AutoWire.

But current behavior:
```
Will add: logic c
```

Look @tests/demo/memory_top.sv .

### Fix go do definition behavior.

@tests/demo/memory_top.sv , there are two modules: memory_top and memory_top2.

However triggering go to definition at `c` inside `memory_top2` leads to variable declaration of `c` inside `memory_top`.

