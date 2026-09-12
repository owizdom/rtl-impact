// three-stage pipeline: a+b -> xor constant -> +1
module pipe (
  input        clk,
  input        rst_n,
  input  [7:0] a,
  input  [7:0] b,
  output reg [7:0] y
);
  reg [7:0] s1, s2;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) s1 <= 8'd0; else s1 <= a + b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) s2 <= 8'd0; else s2 <= s1 ^ 8'h5b;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) y <= 8'd0; else y <= s2 + 8'd1;
`ifdef EXTRA_DEBUG
  (* keep *) reg [7:0] dbg;
  always @(posedge clk) dbg <= s1;
`endif
endmodule
