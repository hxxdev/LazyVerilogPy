### Fix neovim plugin layer.

I have added to my neovim setup @~/.config/nvim/lua/plugins/lazyverilogpy.lua
but our plugin would not be installed and function.
```
vim.pack.add({
    { src = 'https://github.com/hxxdev/LazyVerilogPy', name = 'lazyverilogpy', load = true },
})
require("lazyverilogpy").setup()
```
