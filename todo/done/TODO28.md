### Fix bug when aligning assign operators
If assign operator is already to be aligned at indent grid, `tab_align=true` snaps it to the next indent grid which is not expected. It should be aligned where it was supposed to be.


### Fix formatter behavior:
Current behavior:
```
    for (int i = 0;
    i < 32;
    i++) begin
    end
```

Expected:
```
    for (int i=0; i<32; i++) begin
    end
```

Current behavior:
```
    do begin
        $display("i = %0d", i);
        i++;
    end
    while (i < 5);
```

Expected:
```
    do begin
        $display("i = %0d", i);
        i++;
    end while (i < 5);
```

Take also nested-loops into account.

### Update @docs/format-options.md according to TODO27.md which was already done at previous session.

### Leave commit for each job
