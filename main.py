import flet as ft

def main(page: ft.Page):
    page.title = "Mon Portefeuille"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER

    # Élément d'interface natif
    info_text = ft.Text(value="Mon application native", size=20)

    def action_click(e):
        info_text.value = "Action exécutée !"
        page.update()

    btn = ft.ElevatedButton(text="Rafraîchir", on_click=action_click)

    page.add(
        ft.Column(
            [info_text, btn],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )

ft.app(target=main)
