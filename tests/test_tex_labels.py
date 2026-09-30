import unittest

from qubit_sequencer.tex_labels import tex_to_html


class LabelTests(unittest.TestCase):
    def test_common_device_label_tex(self):
        self.assertEqual(tex_to_html(r"Q_{1,2}"), "Q<sub>1,2</sub>")
        self.assertEqual(tex_to_html(r"\omega_{01}^{(q)}"), "ω<sub>01</sub><sup>(q)</sup>")
        self.assertEqual(tex_to_html(r"C_{\mathrm{XY}}"), "C<sub>XY</sub>")
        self.assertEqual(tex_to_html(r"\Phi_0/2\pi"), "Φ<sub>0</sub>/2π")

    def test_unknown_and_malformed_input_stays_safe_and_visible(self):
        self.assertEqual(tex_to_html(r"\unknown_{1}"), r"\unknown<sub>1</sub>")
        self.assertEqual(tex_to_html("<b>Q</b>"), "&lt;b&gt;Q&lt;/b&gt;")
        self.assertEqual(tex_to_html("Q_{1"), "Q_{1")


if __name__ == "__main__":
    unittest.main()
