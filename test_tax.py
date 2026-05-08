import unittest
from unittest.mock import MagicMock
import sys

# 1. On "simule" Streamlit pour pouvoir tester le moteur fiscal sans lancer l'interface graphique
mock_st = MagicMock()
mock_st.session_state.config = {
    "tax_lim_1": 11294.0, "tax_lim_2": 28797.0, "tax_lim_3": 82341.0, "tax_lim_4": 177106.0,
    "tax_rate_2": 0.11, "tax_rate_3": 0.30, "tax_rate_4": 0.41, "tax_rate_5": 0.45,
    "decote_lim_cel": 2002.0, "decote_base_cel": 906.0,
    "decote_lim_mar": 3300.0, "decote_base_mar": 1493.0
}
sys.modules['streamlit'] = mock_st

from tax_engine import calcul_impot_ir

class TestMoteurFiscal(unittest.TestCase):
    
    def test_impot_celibataire_non_imposable(self):
        """Test n°1 : Un célibataire gagnant 10 000 € ne doit payer aucun impôt."""
        impot = calcul_impot_ir(rev=10000.0, parts=1.0, stat="Célibataire", apply_decote=True)
        self.assertEqual(impot, 0.0, "Erreur : L'impôt devrait être de 0 €")

    def test_impot_celibataire_tranche_2_avec_decote(self):
        """Test n°2 : Un célibataire gagnant 20 000 € (Tranche 11%) doit bénéficier de la décote."""
        impot = calcul_impot_ir(rev=20000.0, parts=1.0, stat="Célibataire", apply_decote=True)
        # Calcul attendu : (20000 - 11294) * 0.11 = 957.66 € avant décote
        # La décote vient réduire ce montant. On vérifie juste qu'il est supérieur à 0 et inférieur à 958.
        self.assertTrue(0 < impot < 958.0, "Erreur : Le calcul de la tranche à 11% ou de la décote a échoué.")

    def test_impot_couple_haut_revenu(self):
        """Test n°3 : Un couple marié (2 parts) gagnant 100 000 €."""
        impot = calcul_impot_ir(rev=100000.0, parts=2.0, stat="Marié(e) / Pacsé(e)", apply_decote=True)
        self.assertTrue(impot > 5000.0, "Erreur : Le calcul pour les couples (2 parts) est faussé.")

if __name__ == '__main__':
    unittest.main()
