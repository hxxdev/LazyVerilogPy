@tests/demo/memory_top.sv AutoWire will declare logic [3:0] c even c type should be inferred as packet_t [3:0].

Also, AutoWire should update the already existing declaration.

When AutoWirePreview is done, variables whose declaration to be update should be shown:

Will update:
c

Also, I recommend changing the alogirhtm to pyslang AST-based instead of regex-based which can't cover corner cases.
