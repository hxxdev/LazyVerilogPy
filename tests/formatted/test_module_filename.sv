// This file should be named test_module_filename.sv to pass the module filename check
module different_name;
// This should trigger a lint error because module name doesn't match filename
endmodule
