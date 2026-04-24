### Hovering on struct type shows weird string 's$<some number>' at the end.
Is this from pyslang library? If so, just leave it. It not, fix it.

typedef struct {
    logic [7:0] addr;
    logic [31:0] data;
    logic valid;
}s$3;


### Are variable declarations inside typedef struct effected by these options?:

var_col1_margin
var_col2_margin
var_col3_margin
var_col4_margin
