### Fix alignment of variable declaration

@tests/demo/memory.sv

alignment of variable declaration changes the variable assignment statements which is not normal.

These two statements are affected by the alignment of variable declaration.

```
        mem             [address]           = data_in                               ;
        mem             [address]           /* test tset <= */ = data_in            ;
```

