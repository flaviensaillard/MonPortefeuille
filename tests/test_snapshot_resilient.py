"""Le robot `snapshot` ne doit plus échouer sur une ligne incohérente.

Le robot était rouge avec :

    ERROR Transactions illisibles : Vente de 22.0 FLXC.L le 2025-01-07
    **Error:** Process completed with exit code 1.

Cause : `calculer_positions()` était appelé SANS liste d'anomalies, donc il levait
`ValueError` à la première vente incohérente et le robot s'arrêtait. Conséquence
concrète : **aucun snapshot n'était jamais écrit**, donc aucune courbe de
performance, donc l'application affichait « aucun historique » en permanence.

Le principe est le même que pour l'application : une ligne douteuse ne doit pas
emporter tout le reste. On la consigne dans une alerte, on continue avec ce qui
est sain, et le snapshot est écrit.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.portfolio import Transaction, calculer_positions


def _vente_sans_achat() -> list[Transaction]:
    """Le cas réel : deux ventes FLXC.L stockées en janvier, avant tout achat."""
    return [
        Transaction("FLXC.L", "vente", dt.date(2025, 1, 7), 22.0, 29.04,
                    6.81, "USD", 632.07),
        Transaction("FLXC.L", "vente", dt.date(2025, 1, 10), 13.0, 34.86,
                    4.53, "USD", 448.65),
    ]


def _achat_puis_vente() -> list[Transaction]:
    return [
        Transaction("IGLN.L", "achat", dt.date(2025, 3, 18), 677.0, 30.4658,
                    111.79, "USD", 20737.14),
        Transaction("FLXC.L", "vente", dt.date(2025, 7, 1), 22.0, 29.04,
                    6.81, "USD", 632.07),
    ]


class TestCalculerPositionsAvecAnomalies:
    def test_sans_liste_leve_toujours(self):
        """Comportement par défaut : on s'arrête. C'est volontaire."""
        with pytest.raises(ValueError, match="sans position"):
            calculer_positions(_vente_sans_achat())

    def test_avec_liste_ne_leve_pas(self):
        anomalies: list[str] = []
        positions = calculer_positions(_vente_sans_achat(), anomalies)
        assert len(anomalies) == 2
        # Les deux ventes sont ignorées, la position reste à zéro.
        assert positions["FLXC.L"].quantite == 0.0

    def test_les_lignes_saines_sont_quand_meme_calculees(self):
        """Le point essentiel : une ligne douteuse n'emporte pas les bonnes."""
        anomalies: list[str] = []
        positions = calculer_positions(_achat_puis_vente(), anomalies)
        assert anomalies, "la vente FLXC.L doit être signalée"
        assert positions["IGLN.L"].quantite == pytest.approx(677.0)
        assert positions["IGLN.L"].cout_total_eur > 0

    def test_le_message_nomme_le_titre_et_la_date(self):
        anomalies: list[str] = []
        calculer_positions(_vente_sans_achat(), anomalies)
        assert "FLXC.L" in anomalies[0]
        assert "2025-01-07" in anomalies[0]
        assert "sans position" in anomalies[0]


class TestRobotSnapshotContinue:
    """Le robot doit écrire le snapshot même avec des anomalies."""

    def test_le_robot_appelle_avec_une_liste(self, monkeypatch):
        """Vérifie dans le code source, sans exécuter Streamlit ni Supabase."""
        import inspect
        import jobs.daily_snapshot as snap

        source = inspect.getsource(snap.main)
        assert "calculer_positions(transactions, anomalies)" in source, (
            "le robot doit passer une liste d'anomalies, sinon une seule ligne "
            "douteuse empêche tout snapshot"
        )
        # L'ancien comportement : `except ValueError: return 1` autour du calcul.
        assert "Transactions illisibles" not in source, (
            "le robot ne doit plus s'arrêter sur une transaction incohérente"
        )
        # Le `return 1` qui reste est légitime : il porte sur les tables absentes.
        assert "Tables absentes" in source

    def test_le_robot_consigne_une_alerte(self, monkeypatch):
        import inspect
        import jobs.daily_snapshot as snap

        source = inspect.getsource(snap.main)
        assert "ajouter_alerte" in source, (
            "l'anomalie doit être enregistrée comme alerte, pas seulement loguée"
        )
