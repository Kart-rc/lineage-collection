package petclinic;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Table;

@Entity
@Table(name = "owners")
public class Owner {
    private Integer id;

    @Column(name = "last_name")
    private String lastName;

    private String city;
}
